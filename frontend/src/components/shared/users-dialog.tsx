import { useState } from "react";
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
import { Copy } from "lucide-react";
import { useCreateUser, useUpdateUser } from "@/api/queries";
import type { User } from "@/api/types";

const ROLES = ["admin", "auditor", "viewer"] as const;

const addSchema = z.object({
  username: z.string().min(1, "Required"),
  email: z.string().email().optional().or(z.literal("")),
  password: z.string().min(1, "Required"),
  roles: z.array(z.string()).min(1, "Pick at least one role"),
});

const editSchema = z.object({
  email: z.string().email().optional().or(z.literal("")),
  roles: z.array(z.string()).min(1, "Pick at least one role"),
});

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** If set, we're editing; otherwise we're adding. */
  user?: User;
}

export function UsersDialog({ open, onOpenChange, user }: Props) {
  const isEdit = !!user;
  const create = useCreateUser();
  const update = useUpdateUser();
  const [tempPassword, setTempPassword] = useState<string | null>(null);

  const addForm = useForm<z.infer<typeof addSchema>>({
    resolver: zodResolver(addSchema),
    defaultValues: { username: "", email: "", password: "", roles: ["viewer"] },
  });

  const editForm = useForm<z.infer<typeof editSchema>>({
    resolver: zodResolver(editSchema),
    defaultValues: {
      email: user?.email ?? "",
      roles: user?.roles ?? ["viewer"],
    },
    values: isEdit
      ? { email: user?.email ?? "", roles: user?.roles ?? ["viewer"] }
      : undefined,
  });

  const onAddSubmit = addForm.handleSubmit(async (values) => {
    try {
      const res = await create.mutateAsync(values);
      if (res?.temp_password) setTempPassword(res.temp_password);
      else {
        onOpenChange(false);
        addForm.reset();
      }
    } catch (err) {
      addForm.setError("root", { message: (err as Error).message });
    }
  });

  const onEditSubmit = editForm.handleSubmit(async (values) => {
    if (!user) return;
    try {
      await update.mutateAsync({ id: user.id, ...values });
      onOpenChange(false);
    } catch (err) {
      editForm.setError("root", { message: (err as Error).message });
    }
  });

  const close = () => {
    onOpenChange(false);
    setTempPassword(null);
    addForm.reset();
  };

  return (
    <Dialog open={open} onOpenChange={(v) => (v ? onOpenChange(true) : close())}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{tempPassword ? "Temporary password" : isEdit ? "Edit user" : "Add user"}</DialogTitle>
        </DialogHeader>

        {tempPassword ? (
          <div>
            <p className="mb-3 text-sm text-muted-foreground">
              Share this once — it won't be shown again.
            </p>
            <div className="flex items-center gap-2 rounded-md border border-border bg-muted/40 p-3">
              <code className="flex-1 font-mono text-sm break-all">{tempPassword}</code>
              <Button
                size="icon"
                variant="ghost"
                onClick={() => navigator.clipboard.writeText(tempPassword)}
                aria-label="Copy"
              >
                <Copy className="h-4 w-4" />
              </Button>
            </div>
            <div className="mt-4 flex justify-end">
              <Button onClick={close}>Done</Button>
            </div>
          </div>
        ) : isEdit ? (
          <Form {...editForm}>
            <form onSubmit={onEditSubmit} className="space-y-4" noValidate>
              {/* Username is immutable post-creation — render as a plain
                  read-only field rather than via FormLabel/FormItem,
                  which would call useFormField() outside any FormField
                  context and crash the dialog. */}
              <div className="space-y-2">
                <label className="text-sm font-medium leading-none">Username</label>
                <Input value={user?.username ?? ""} disabled />
              </div>
              <FormField
                control={editForm.control}
                name="email"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Email</FormLabel>
                    <FormControl>
                      <Input type="email" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={editForm.control}
                name="roles"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Roles</FormLabel>
                    <div className="flex flex-wrap gap-3">
                      {ROLES.map((r) => (
                        <label key={r} className="flex items-center gap-2 text-sm">
                          <Checkbox
                            checked={field.value.includes(r)}
                            onCheckedChange={(checked) => {
                              field.onChange(
                                checked ? [...field.value, r] : field.value.filter((x) => x !== r),
                              );
                            }}
                          />
                          {r}
                        </label>
                      ))}
                    </div>
                    <FormMessage />
                  </FormItem>
                )}
              />
              {editForm.formState.errors.root ? (
                <p className="text-sm text-destructive">{editForm.formState.errors.root.message}</p>
              ) : null}
              <div className="flex justify-end gap-2">
                <Button type="button" variant="ghost" onClick={close}>Cancel</Button>
                <Button type="submit" disabled={update.isPending}>
                  {update.isPending ? "Saving…" : "Save"}
                </Button>
              </div>
            </form>
          </Form>
        ) : (
          <Form {...addForm}>
            <form onSubmit={onAddSubmit} className="space-y-4" noValidate>
              <FormField
                control={addForm.control}
                name="username"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Username</FormLabel>
                    <FormControl><Input {...field} /></FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={addForm.control}
                name="email"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Email</FormLabel>
                    <FormControl><Input type="email" {...field} /></FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={addForm.control}
                name="password"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Password</FormLabel>
                    <FormControl><Input type="password" autoComplete="new-password" {...field} /></FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={addForm.control}
                name="roles"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Roles</FormLabel>
                    <div className="flex flex-wrap gap-3">
                      {ROLES.map((r) => (
                        <label key={r} className="flex items-center gap-2 text-sm">
                          <Checkbox
                            checked={field.value.includes(r)}
                            onCheckedChange={(checked) => {
                              field.onChange(
                                checked ? [...field.value, r] : field.value.filter((x) => x !== r),
                              );
                            }}
                          />
                          {r}
                        </label>
                      ))}
                    </div>
                    <FormMessage />
                  </FormItem>
                )}
              />
              {addForm.formState.errors.root ? (
                <p className="text-sm text-destructive">{addForm.formState.errors.root.message}</p>
              ) : null}
              <div className="flex justify-end gap-2">
                <Button type="button" variant="ghost" onClick={close}>Cancel</Button>
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
