import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { toast } from "sonner";
import { Eye, EyeOff } from "lucide-react";
import { PageHeader } from "@/components/shared/page-header";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { useChangePassword, useUpdateEmail } from "@/api/queries";
import { useMe } from "@/hooks/use-me";

const emailSchema = z.object({ email: z.string().email() });
const passwordSchema = z
  .object({
    current_password: z.string().min(1, "Required"),
    new_password: z.string().min(8, "Minimum 8 characters"),
    confirm: z.string(),
  })
  .refine((v) => v.new_password === v.confirm, {
    message: "Passwords don't match",
    path: ["confirm"],
  });

function PasswordInput({
  field,
  autoComplete,
  ...rest
}: {
  field: React.ComponentProps<typeof Input>;
  autoComplete: string;
} & React.ComponentProps<typeof Input>) {
  const [show, setShow] = useState(false);
  return (
    <div className="relative">
      {/* `rest` carries the id + aria-* that FormControl's Slot injects onto
          its child; forward them to the real input so the FormLabel's
          htmlFor associates (and screen readers / getByLabelText resolve it). */}
      <Input
        type={show ? "text" : "password"}
        autoComplete={autoComplete}
        {...rest}
        {...field}
      />
      <button
        type="button"
        onClick={() => setShow((p) => !p)}
        className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
        aria-label={show ? "Hide password" : "Show password"}
      >
        {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
      </button>
    </div>
  );
}

export default function ProfilePage() {
  const { me } = useMe();
  const updateEmail = useUpdateEmail();
  const changePassword = useChangePassword();

  const emailForm = useForm<z.infer<typeof emailSchema>>({
    resolver: zodResolver(emailSchema),
    defaultValues: { email: me?.email ?? "" },
    values: { email: me?.email ?? "" },
  });

  const passwordForm = useForm<z.infer<typeof passwordSchema>>({
    resolver: zodResolver(passwordSchema),
    defaultValues: { current_password: "", new_password: "", confirm: "" },
  });

  const onEmail = emailForm.handleSubmit(async (v) => {
    try {
      await updateEmail.mutateAsync({ email: v.email });
      toast.success("Email updated");
    } catch (err) {
      toast.error((err as Error).message || "Failed to update email");
    }
  });

  const onPassword = passwordForm.handleSubmit(async (v) => {
    try {
      await changePassword.mutateAsync({
        current_password: v.current_password,
        new_password: v.new_password,
      });
      toast.success("Password changed");
      passwordForm.reset();
    } catch (err) {
      toast.error((err as Error).message || "Failed to change password");
    }
  });

  return (
    <>
      <PageHeader title="Profile" description="Update your email address or password" />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardContent className="p-6">
            <h2 className="mb-4 text-sm font-semibold">Email</h2>
            <Form {...emailForm}>
              <form onSubmit={onEmail} className="space-y-4" noValidate>
                <FormField
                  control={emailForm.control}
                  name="email"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Email</FormLabel>
                      <FormControl><Input type="email" {...field} /></FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <Button type="submit" disabled={updateEmail.isPending}>
                  {updateEmail.isPending ? "Saving…" : "Save email"}
                </Button>
              </form>
            </Form>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-6">
            <h2 className="mb-4 text-sm font-semibold">Change password</h2>
            <Form {...passwordForm}>
              <form onSubmit={onPassword} className="space-y-4" noValidate>
                <FormField
                  control={passwordForm.control}
                  name="current_password"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Current password</FormLabel>
                      <FormControl>
                        <PasswordInput field={field} autoComplete="current-password" />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={passwordForm.control}
                  name="new_password"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>New password</FormLabel>
                      <FormControl>
                        <PasswordInput field={field} autoComplete="new-password" />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={passwordForm.control}
                  name="confirm"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Confirm new password</FormLabel>
                      <FormControl>
                        <PasswordInput field={field} autoComplete="new-password" />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <Button type="submit" disabled={changePassword.isPending}>
                  {changePassword.isPending ? "Saving…" : "Change password"}
                </Button>
              </form>
            </Form>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
