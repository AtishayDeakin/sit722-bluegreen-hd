import {
  render,
  screen,
} from "@testing-library/react";
import {
  afterEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import ReleaseBadge from "../components/ReleaseBadge";

const mockFetch = (response) => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(response)
  );
};

describe("ReleaseBadge", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows the colour and short version of the release", async () => {
    mockFetch({
      ok: true,
      json: async () => ({
        color: "green",
        version: "3f9c2ab1d4e5f6a7b8c9",
      }),
    });

    render(<ReleaseBadge />);

    expect(
      await screen.findByText("green · 3f9c2ab")
    ).toBeInTheDocument();
  });

  it("renders nothing when the release endpoint is unavailable", async () => {
    mockFetch({ ok: false });

    render(<ReleaseBadge />);

    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(
      screen.queryByTestId("release-badge")
    ).not.toBeInTheDocument();
  });
});
