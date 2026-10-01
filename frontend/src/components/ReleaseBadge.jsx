import { useEffect, useState } from "react";
import { Chip } from "@mui/material";

// Shows which blue/green release is serving the page, e.g. "green · 3f9c2ab".
// Reads /release from nginx and refreshes every few seconds, so the badge
// changes colour on its own when the pipeline switches production traffic.
const REFRESH_MS = 5000;

const colours = {
  blue: "#1565c0",
  green: "#2e7d32",
};

const ReleaseBadge = () => {
  const [release, setRelease] = useState(null);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const response = await fetch("/release", { cache: "no-store" });

        if (!response.ok) {
          return;
        }

        const data = await response.json();

        if (!cancelled) {
          setRelease(data);
        }
      } catch {
        // Not running behind nginx (e.g. vite dev server): show nothing.
      }
    };

    load();
    const timer = setInterval(load, REFRESH_MS);

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  if (!release?.color) {
    return null;
  }

  const shortVersion = (release.version || "").slice(0, 7);

  return (
    <Chip
      data-testid="release-badge"
      label={`${release.color} · ${shortVersion}`}
      size="small"
      sx={{
        position: "fixed",
        bottom: 12,
        right: 12,
        zIndex: 2000,
        fontWeight: 600,
        color: "white",
        backgroundColor: colours[release.color] || "#616161",
      }}
    />
  );
};

export default ReleaseBadge;
