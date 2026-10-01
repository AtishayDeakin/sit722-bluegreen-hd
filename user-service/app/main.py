import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from app.db import Base, engine
from app.observability import setup_observability
from app.models import User, UserRole
from app.routers import auth, users
from app.security import hash_password


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


def initialise_database() -> None:
    maximum_attempts = 10
    retry_delay_seconds = 5

    for attempt in range(1, maximum_attempts + 1):
        try:
            Base.metadata.create_all(bind=engine)

            logger.info(
                "Database connection established successfully."
            )

            return

        # Blue and green pods share one database, so two pods can race
        # to create the same tables. Treat that like a connection error
        # and retry instead of crashing the pod.
        except (OperationalError, IntegrityError, ProgrammingError):
            logger.warning(
                "Database connection failed. Attempt %s of %s.",
                attempt,
                maximum_attempts,
            )

            if attempt == maximum_attempts:
                logger.exception(
                    "Unable to connect to the database."
                )
                raise

            time.sleep(retry_delay_seconds)


def create_default_admin() -> None:
    admin_username = os.getenv(
        "DEFAULT_ADMIN_USERNAME",
        "admin",
    )

    admin_email = os.getenv(
        "DEFAULT_ADMIN_EMAIL",
        "admin@koalatech.edu.au",
    )

    admin_password = os.getenv(
        "DEFAULT_ADMIN_PASSWORD",
        "AdminPassword123!",
    )

    with Session(engine) as db:
        existing_admin = db.scalar(
            select(User).where(
                User.username == admin_username
            )
        )

        if existing_admin is not None:
            logger.info(
                "Default administrator account already exists."
            )
            return

        admin = User(
            username=admin_username,
            email=admin_email,
            hashed_password=hash_password(admin_password),
            role=UserRole.ADMIN,
            is_active=True,
        )

        db.add(admin)

        try:
            db.commit()
        except IntegrityError:
            # Another replica created the admin at the same moment.
            db.rollback()
            logger.info(
                "Default administrator account was created by another replica."
            )
            return

        logger.info(
            "Default administrator account created."
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialise_database()
    create_default_admin()

    yield


app = FastAPI(
    title="KoalaTech University User Service",
    description=(
        "Manages user accounts, authentication and "
        "role-based access for KoalaTech University."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


app.include_router(auth.router)
app.include_router(users.router)
setup_observability(app, engine, "user-service")


@app.get(
    "/",
    tags=["Health"],
)
def root() -> dict[str, str]:
    return {
        "message": "KoalaTech University User Service is running."
    }


@app.get(
    "/health",
    tags=["Health"],
)
def health_check() -> dict[str, str]:
    return {
        "status": "healthy",
        "service": "user-service",
    }