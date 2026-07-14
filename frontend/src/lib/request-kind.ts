// Mapping from the backend's RequestKind enum to user-facing chip labels
// and one-line synthesis text for entries that would otherwise render empty.
// The kind enum itself lives on the wire — see api/types.ts and the proxy's
// _request_kind() in src/kyde/server.py.

import type { RequestKind } from "@/api/types";

export interface KindDescriptor {
  // Short uppercase tag rendered in the row chip.
  chip: string;
  // Tone hint for the chip; mapped to a Tailwind class palette below.
  tone: "neutral" | "warn" | "danger" | "info";
  // One-liner explaining why the entry has no visible body. The
  // synthesise() helper below stitches this together with model/tokens.
  reason: string | null;
}

const KIND_DESCRIPTORS: Record<RequestKind, KindDescriptor> = {
  // Chat-shaped (Phase A)
  chat: {
    chip: "CHAT",
    tone: "neutral",
    reason: null,
  },
  chat_tool_only: {
    chip: "TOOL-ONLY",
    tone: "info",
    reason: "assistant replied with tool calls only — no text content",
  },
  chat_streaming_partial: {
    chip: "STREAM-INCOMPLETE",
    tone: "warn",
    reason: "streaming response captured no assistant text — SSE may have been interrupted",
  },
  chat_empty_request: {
    chip: "EMPTY-REQUEST",
    tone: "warn",
    reason: "client sent no user/assistant messages — only a system prompt, or messages=[]",
  },
  chat_empty_content: {
    chip: "EMPTY-CONTENT",
    tone: "warn",
    reason: "messages were present but every content field was blank",
  },
  policy_block: {
    chip: "BLOCKED",
    tone: "danger",
    reason: "request rejected by policy and never sent upstream",
  },

  // Non-chat (Phase B2). `reason` describes the endpoint role, so the
  // synthesis line on an embedding row reads naturally even without a
  // visible request body. tone='info' across the set — these aren't
  // failure modes, they're just non-chat traffic.
  embedding: {
    chip: "EMBEDDING",
    tone: "info",
    reason: "vector embedding call",
  },
  moderation: {
    chip: "MODERATION",
    tone: "info",
    reason: "content moderation classifier call",
  },
  models_list: {
    chip: "MODELS-LIST",
    tone: "info",
    reason: "model catalog lookup",
  },
  tokens_count: {
    chip: "COUNT-TOKENS",
    tone: "info",
    reason: "token-counting capability call",
  },
  audio_transcription: {
    chip: "STT",
    tone: "info",
    reason: "audio transcription (speech-to-text)",
  },
  audio_translation: {
    chip: "AUDIO-XL",
    tone: "info",
    reason: "audio translation",
  },
  audio_speech: {
    chip: "TTS",
    tone: "info",
    reason: "audio synthesis (text-to-speech)",
  },
  image_generation: {
    chip: "IMAGE-GEN",
    tone: "info",
    reason: "image generation",
  },
  image_edit: {
    chip: "IMAGE-EDIT",
    tone: "info",
    reason: "image edit",
  },
  image_variation: {
    chip: "IMAGE-VAR",
    tone: "info",
    reason: "image variation",
  },
  legacy_completion: {
    chip: "LEGACY-COMP",
    tone: "info",
    reason: "legacy text-completion call (pre-chat API)",
  },
  file_op: {
    chip: "FILE-OP",
    tone: "info",
    reason: "file upload / download / management call",
  },
  fine_tuning: {
    chip: "FINE-TUNE",
    tone: "info",
    reason: "fine-tuning job call",
  },

  unknown: {
    chip: "UNCLASSIFIED",
    tone: "neutral",
    reason: "predates the request_kind classifier — kind couldn't be derived from stored fields",
  },
};

export function describeKind(kind: RequestKind | undefined | null): KindDescriptor {
  if (!kind) return KIND_DESCRIPTORS.unknown;
  return KIND_DESCRIPTORS[kind] ?? KIND_DESCRIPTORS.unknown;
}

// Whether a row of this kind should ever be expected to carry visible
// chat body text. False means the UI should render the kind-specific
// synthesis line in place of the body, rather than blank space.
export function hasChatBody(kind: RequestKind | undefined | null): boolean {
  return kind === "chat" || kind === undefined || kind === null;
}

export function synthesise(
  kind: RequestKind | undefined | null,
  {
    model,
    upstream,
    promptTokens,
    completionTokens,
    toolCount,
    firstTool,
  }: {
    model?: string;
    upstream?: string;
    promptTokens?: number;
    completionTokens?: number;
    toolCount?: number;
    firstTool?: string | null;
  },
): string {
  const desc = describeKind(kind);
  const bits: string[] = [];
  if (desc.reason) bits.push(desc.reason);

  // For tool-only turns the first tool name is the actually-useful signal —
  // surface it inline so the reader doesn't have to open the entry detail.
  if (kind === "chat_tool_only" && firstTool && firstTool !== "-") {
    const n = (toolCount ?? 0) > 1 ? ` (+${(toolCount ?? 1) - 1} more)` : "";
    bits.push(`first tool: ${firstTool}${n}`);
  }

  const provenance: string[] = [];
  if (model) provenance.push(model);
  if (upstream) provenance.push(upstream);
  if (typeof promptTokens === "number" || typeof completionTokens === "number") {
    provenance.push(
      `${promptTokens ?? 0} → ${completionTokens ?? 0} tokens`,
    );
  }
  if (provenance.length > 0) bits.push(provenance.join(" · "));

  return bits.join(" · ");
}

export const KIND_TONE_CLASSES: Record<KindDescriptor["tone"], string> = {
  neutral: "bg-muted text-muted-foreground border-border",
  info: "bg-primary/10 text-primary border-primary/30",
  warn: "bg-warning/10 text-warning border-warning/30",
  danger: "bg-destructive/10 text-destructive border-destructive/30",
};
