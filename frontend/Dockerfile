# ---- Build stage ----------------------------------------------------------
# The React build output is plain static files, so this stage always runs on
# the build machine's own CPU ($BUILDPLATFORM). Only the small nginx stage is
# built per target architecture, which keeps multi-arch builds fast.
FROM --platform=$BUILDPLATFORM node:22-alpine AS build

WORKDIR /app

COPY package*.json ./

RUN npm ci

COPY . .

ARG VITE_USER_SERVICE_URL=/api/users
ARG VITE_STUDENT_SERVICE_URL=/api/students
ARG VITE_LECTURER_SERVICE_URL=/api/lecturers
ARG VITE_COURSE_SERVICE_URL=/api/courses
ARG VITE_ENROLLMENT_SERVICE_URL=/api/enrollments

ENV VITE_USER_SERVICE_URL=${VITE_USER_SERVICE_URL}
ENV VITE_STUDENT_SERVICE_URL=${VITE_STUDENT_SERVICE_URL}
ENV VITE_LECTURER_SERVICE_URL=${VITE_LECTURER_SERVICE_URL}
ENV VITE_COURSE_SERVICE_URL=${VITE_COURSE_SERVICE_URL}
ENV VITE_ENROLLMENT_SERVICE_URL=${VITE_ENROLLMENT_SERVICE_URL}

RUN npm run build

# ---- Runtime stage --------------------------------------------------------
FROM nginx:1.27-alpine

# Defaults suit docker compose. Kubernetes overrides them per colour.
ENV BACKEND_SUFFIX="" \
    APP_COLOR="local" \
    APP_VERSION="dev"

# The nginx image renders /etc/nginx/templates/*.template into conf.d at start.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
