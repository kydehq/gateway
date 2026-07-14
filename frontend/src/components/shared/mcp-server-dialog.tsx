import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { useCreateMcpServer, useUpdateMcpServer } from "@/api/queries";
import type { McpServer } from "@/api/types";

// Mirrors the backend regex in mcp_registry._NAME_RE — kept loose enough
// to surface the same error message before the round-trip.
const NAME_RE = /^[a-z0-9][a-z0-9_-]{0,62}$/;

const baseSchema = z.object({
  upstream_url: z
    .string()
    .min(1, "Required")
    .refine(
      (v) => v.startsWith("http://") || v.startsWith("https://"),
      "Must start with http:// or https://",
    ),
  enabled: z.boolean(),
});

const addSchema = baseSchema.extend({
  name: z
    .string()
    .min(1, "Required")
    .regex(
      NAME_RE,
      "Lowercase letters, digits, hyphens, underscores; 1–63 chars",
    ),
});

const editSchema = baseSchema;

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** If set, we're editing; otherwise we're adding. Name cannot be changed. */
  server?: McpServer;
}

export function McpServerDialog({ open, onOpenChange, server }: Props) {
  const isEdit = !!server;
  const create = useCreateMcpServer();
  const update = useUpdateMcpServer();

  const addForm = useForm<z.infer<typeof addSchema>>({
    resolver: zodResolver(addSchema),
    defaultValues: { name: "", upstream_url: "", enabled: true },
  });

  const editForm = useForm<z.infer<typeof editSchema>>({
    resolver: zodResolver(editSchema),
    defaultValues: {
      upstream_url: server?.upstream_url ?? "",
      enabled: server?.enabled ?? true,
    },
    values: isEdit
      ? { upstream_url: server!.upstream_url, enabled: server!.enabled }
      : undefined,
  });

  const onAddSubmit = addForm.handleSubmit(async (values) => {
    try {
      await create.mutateAsync(values);
      onOpenChange(false);
      addForm.reset();
    } catch (err) {
      addForm.setError("root", { message: (err as Error).message });
    }
  });

  const onEditSubmit = editForm.handleSubmit(async (values) => {
    if (!server) return;
    try {
      await update.mutateAsync({ name: server.name, ...values });
      onOpenChange(false);
    } catch (err) {
      editForm.setError("root", { message: (err as Error).message });
    }
  });

  const close = () => {
    onOpenChange(false);
    addForm.reset();
  };

  return (
    <Dialog open={open} onOpenChange={(v) => (v ? onOpenChange(true) : close())}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit MCP server" : "Add MCP server"}</DialogTitle>
        </DialogHeader>

        {isEdit ? (
          <Form {...editForm}>
            <form onSubmit={onEditSubmit} className="space-y-4">
              {/* Name is read-only on edit — rendered as plain markup so
                  it doesn't try to subscribe to a form field that doesn't
                  exist in editSchema. FormLabel/FormMessage call
                  useFormField under the hood and would throw otherwise. */}
              <div className="space-y-2">
                <label className="text-sm font-medium">Name</label>
                <Input value={server!.name} disabled />
                <p className="text-[11px] text-muted-foreground">
                  Routing handle (delete + recreate to rename).
                </p>
              </div>
              <FormField
                control={editForm.control}
                name="upstream_url"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Upstream URL</FormLabel>
                    <FormControl>
                      <Input placeholder="https://mcp.example.com/mcp" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={editForm.control}
                name="enabled"
                render={({ field }) => (
                  <FormItem>
                    <label className="flex items-center gap-2 text-sm">
                      <Checkbox
                        checked={field.value}
                        onCheckedChange={(v) => field.onChange(v === true)}
                      />
                      Enabled
                    </label>
                    <FormMessage />
                  </FormItem>
                )}
              />
              {editForm.formState.errors.root ? (
                <p className="text-sm text-destructive">
                  {editForm.formState.errors.root.message}
                </p>
              ) : null}
              <div className="flex justify-end gap-2">
                <Button type="button" variant="ghost" onClick={close}>
                  Cancel
                </Button>
                <Button type="submit" disabled={update.isPending}>
                  {update.isPending ? "Saving…" : "Save"}
                </Button>
              </div>
            </form>
          </Form>
        ) : (
          <Form {...addForm}>
            <form onSubmit={onAddSubmit} className="space-y-4">
              <FormField
                control={addForm.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Name</FormLabel>
                    <FormControl>
                      <Input placeholder="notion" {...field} />
                    </FormControl>
                    <p className="text-[11px] text-muted-foreground">
                      Appears in the gateway path: /mcp/<code>{field.value || "name"}</code>
                    </p>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={addForm.control}
                name="upstream_url"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Upstream URL</FormLabel>
                    <FormControl>
                      <Input placeholder="https://mcp.example.com/mcp" {...field} />
                    </FormControl>
                    <p className="text-[11px] text-muted-foreground">
                      Kyde forwards the agent's Authorization header unchanged.
                      Configure credentials in your agent, not here.
                    </p>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={addForm.control}
                name="enabled"
                render={({ field }) => (
                  <FormItem>
                    <label className="flex items-center gap-2 text-sm">
                      <Checkbox
                        checked={field.value}
                        onCheckedChange={(v) => field.onChange(v === true)}
                      />
                      Enabled
                    </label>
                    <FormMessage />
                  </FormItem>
                )}
              />
              {addForm.formState.errors.root ? (
                <p className="text-sm text-destructive">
                  {addForm.formState.errors.root.message}
                </p>
              ) : null}
              <div className="flex justify-end gap-2">
                <Button type="button" variant="ghost" onClick={close}>
                  Cancel
                </Button>
                <Button type="submit" disabled={create.isPending}>
                  {create.isPending ? "Creating…" : "Create"}
                </Button>
              </div>
            </form>
          </Form>
        )}
      </DialogContent>
    </Dialog>
  );
}
