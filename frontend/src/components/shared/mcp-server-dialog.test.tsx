import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { McpServer } from "@/api/types";

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

const SERVER: McpServer = {
  id: "1",
  name: "files",
  upstream_url: "http://mcp-files:9000",
  enabled: true,
  created_at: "2026-06-01T08:00:00Z",
  created_by: 1,
  last_call_at: null,
  last_error_at: null,
  last_error_status: null,
  last_error_snippet: null,
};

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

  it("can create a server disabled via the checkbox", async () => {
    const user = userEvent.setup();
    render(<McpServerDialog open onOpenChange={vi.fn()} />);

    await user.type(screen.getByPlaceholderText("notion"), NAME);
    await user.type(
      screen.getByPlaceholderText("https://mcp.example.com/mcp"),
      URL,
    );
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Create" }));

    await vi.waitFor(() =>
      expect(createSpy).toHaveBeenCalledWith({
        name: NAME,
        upstream_url: URL,
        enabled: false,
      }),
    );
  });

  it("renders the backend error as a root message", async () => {
    createSpy.mockRejectedValue(new Error("name_taken"));
    const user = userEvent.setup();
    render(<McpServerDialog open onOpenChange={vi.fn()} />);

    await user.type(screen.getByPlaceholderText("notion"), NAME);
    await user.type(
      screen.getByPlaceholderText("https://mcp.example.com/mcp"),
      URL,
    );
    await user.click(screen.getByRole("button", { name: "Create" }));

    expect(await screen.findByText("name_taken")).toBeInTheDocument();
  });
});

describe("McpServerDialog — edit", () => {
  it("shows the immutable name and prefilled upstream URL", () => {
    render(<McpServerDialog open onOpenChange={vi.fn()} server={SERVER} />);
    expect(screen.getByText("Edit MCP server")).toBeInTheDocument();
    expect(screen.getByDisplayValue("files")).toBeDisabled();
    expect(screen.getByDisplayValue("http://mcp-files:9000")).toBeInTheDocument();
  });

  it("saves URL and enabled changes keyed by the server name", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    render(<McpServerDialog open onOpenChange={onOpenChange} server={SERVER} />);

    const url = screen.getByDisplayValue("http://mcp-files:9000");
    await user.clear(url);
    await user.type(url, "https://mcp-files:9443");
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Save" }));

    await vi.waitFor(() =>
      expect(updateSpy).toHaveBeenCalledWith({
        name: "files",
        upstream_url: "https://mcp-files:9443",
        enabled: false,
      }),
    );
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("validates the URL on edit and surfaces backend failures", async () => {
    const user = userEvent.setup();
    render(<McpServerDialog open onOpenChange={vi.fn()} server={SERVER} />);

    const url = screen.getByDisplayValue("http://mcp-files:9000");
    await user.clear(url);
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Required")).toBeInTheDocument();
    expect(updateSpy).not.toHaveBeenCalled();

    updateSpy.mockRejectedValue(new Error("409 conflict"));
    await user.type(url, URL);
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("409 conflict")).toBeInTheDocument();
  });

  it("closes without saving via Cancel", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    render(<McpServerDialog open onOpenChange={onOpenChange} server={SERVER} />);
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(updateSpy).not.toHaveBeenCalled();
  });
});
