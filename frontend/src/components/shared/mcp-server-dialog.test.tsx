import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Mock the mutation hooks so valid submits are observable and no network fires.
const { createSpy, updateSpy } = vi.hoisted(() => ({
  createSpy: vi.fn(),
  updateSpy: vi.fn(),
}));
vi.mock("@/api/queries", () => ({
  useCreateMcpServer: () => ({ mutateAsync: createSpy, isPending: false }),
  useUpdateMcpServer: () => ({ mutateAsync: updateSpy, isPending: false }),
}));

import { McpServerDialog } from "./mcp-server-dialog";

const NAME = "notion";
const URL = "https://mcp.example.com";

beforeEach(() => {
  createSpy.mockReset().mockResolvedValue(undefined);
  updateSpy.mockReset().mockResolvedValue(undefined);
});

describe("McpServerDialog — add validation (mirrors backend mcp_registry._NAME_RE)", () => {
  it("rejects an uppercase / invalid name", async () => {
    const user = userEvent.setup();
    render(<McpServerDialog open onOpenChange={vi.fn()} />);

    await user.type(screen.getByPlaceholderText("notion"), "Notion"); // uppercase
    await user.type(
      screen.getByPlaceholderText("https://mcp.example.com/mcp"),
      URL,
    );
    await user.click(screen.getByRole("button", { name: "Create" }));

    expect(await screen.findByText(/Lowercase letters/)).toBeInTheDocument();
    expect(createSpy).not.toHaveBeenCalled();
  });

  it("rejects an upstream URL without an http(s) scheme", async () => {
    const user = userEvent.setup();
    render(<McpServerDialog open onOpenChange={vi.fn()} />);

    await user.type(screen.getByPlaceholderText("notion"), NAME);
    await user.type(
      screen.getByPlaceholderText("https://mcp.example.com/mcp"),
      "ftp://nope",
    );
    await user.click(screen.getByRole("button", { name: "Create" }));

    expect(
      await screen.findByText(/Must start with http:\/\/ or https:\/\//),
    ).toBeInTheDocument();
    expect(createSpy).not.toHaveBeenCalled();
  });

  it("submits valid input through the schema", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    render(<McpServerDialog open onOpenChange={onOpenChange} />);

    await user.type(screen.getByPlaceholderText("notion"), NAME);
    await user.type(
      screen.getByPlaceholderText("https://mcp.example.com/mcp"),
      URL,
    );
    await user.click(screen.getByRole("button", { name: "Create" }));

    await vi.waitFor(() =>
      expect(createSpy).toHaveBeenCalledWith({
        name: NAME,
        upstream_url: URL,
        enabled: true,
      }),
    );
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
