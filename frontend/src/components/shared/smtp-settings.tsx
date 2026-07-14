import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Eye, EyeOff, Send, Users as UsersIcon } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useSendTestEmail,
  useSettings,
  useUpdateSetting,
  useUsers,
} from "@/api/queries";
import type { SettingEntry } from "@/api/types";

type SmtpKey =
  | "SMTP_ENABLED"
  | "SMTP_HOST"
  | "SMTP_PORT"
  | "SMTP_ENCRYPTION"
  | "SMTP_USERNAME"
  | "SMTP_PASSWORD_ENC"
  | "SMTP_FROM_ADDRESS"
  | "SMTP_FROM_NAME"
  | "SMTP_REPLY_TO"
  | "SMTP_TLS_VERIFY"
  | "SMTP_TIMEOUT_SECONDS"
  | "SMTP_TRIGGER_POLICY"
  | "SMTP_MIN_SCORE";

// Local form state: everything is stored as a string for the value + a
// boolean for the `enabled/verify` flags. The save handler rounds-trips
// each through a PATCH whose body shape is `{value: string}` either way.
interface FormState {
  SMTP_ENABLED: boolean;
  SMTP_HOST: string;
  SMTP_PORT: string;
  SMTP_ENCRYPTION: string;
  SMTP_USERNAME: string;
  // Secret. Plaintext typed by the user; never pre-filled. "" on submit
  // means "keep whatever is currently stored".
  SMTP_PASSWORD_ENC: string;
  SMTP_FROM_ADDRESS: string;
  SMTP_FROM_NAME: string;
  SMTP_REPLY_TO: string;
  SMTP_TLS_VERIFY: boolean;
  SMTP_TIMEOUT_SECONDS: string;
  SMTP_TRIGGER_POLICY: string;
  SMTP_MIN_SCORE: string;
}

function entriesToFormState(entries: SettingEntry[] | undefined): FormState {
  const pick = (key: SmtpKey) => entries?.find((e) => e.key === key);
  const s = (key: SmtpKey, fallback = "") =>
    String(pick(key)?.value ?? pick(key)?.default ?? fallback);
  const b = (key: SmtpKey, fallback = false) => {
    const e = pick(key);
    if (!e) return fallback;
    return typeof e.value === "boolean" ? e.value : String(e.value) === "true";
  };
  return {
    SMTP_ENABLED: b("SMTP_ENABLED", false),
    SMTP_HOST: s("SMTP_HOST"),
    SMTP_PORT: s("SMTP_PORT", "587"),
    SMTP_ENCRYPTION: s("SMTP_ENCRYPTION", "starttls"),
    SMTP_USERNAME: s("SMTP_USERNAME"),
    SMTP_PASSWORD_ENC: "", // never pre-fill secrets
    SMTP_FROM_ADDRESS: s("SMTP_FROM_ADDRESS"),
    SMTP_FROM_NAME: s("SMTP_FROM_NAME", "Kyde Gateway Alerts"),
    SMTP_REPLY_TO: s("SMTP_REPLY_TO"),
    SMTP_TLS_VERIFY: b("SMTP_TLS_VERIFY", true),
    SMTP_TIMEOUT_SECONDS: s("SMTP_TIMEOUT_SECONDS", "10"),
    SMTP_TRIGGER_POLICY: s("SMTP_TRIGGER_POLICY", "first_detection"),
    SMTP_MIN_SCORE: s("SMTP_MIN_SCORE", "0.8"),
  };
}

export function SmtpSettings() {
  const { data: entries, isLoading } = useSettings();
  const { data: users } = useUsers(false);
  const updateSetting = useUpdateSetting();
  const sendTest = useSendTestEmail();

  // Track whether a password is already stored (server-side), so we
  // can render "••••••••" instead of an empty input by default.
  const passwordIsSet = useMemo(
    () => entries?.find((e) => e.key === "SMTP_PASSWORD_ENC")?.is_set ?? false,
    [entries],
  );

  const [form, setForm] = useState<FormState>(() => entriesToFormState(undefined));
  const [dirty, setDirty] = useState<Record<string, true>>({});
  const [replacingPassword, setReplacingPassword] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [saving, setSaving] = useState(false);

  // Re-seed when entries load / refresh, unless the user has already
  // edited that key (keeps typing from being clobbered by a refetch).
  useEffect(() => {
    if (!entries) return;
    const fresh = entriesToFormState(entries);
    setForm((prev) => {
      const next: FormState = { ...prev };
      (Object.keys(fresh) as (keyof FormState)[]).forEach((k) => {
        if (!dirty[k]) (next[k] as FormState[typeof k]) = fresh[k];
      });
      return next;
    });
  }, [entries]);

  const auditorCount = useMemo(
    () =>
      (users ?? []).filter(
        (u) => u.enabled !== false && (u.roles ?? []).includes("auditor") && u.email,
      ).length,
    [users],
  );

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((p) => ({ ...p, [key]: value }));
    setDirty((d) => ({ ...d, [key]: true }));
  }

  async function onSave() {
    setSaving(true);
    try {
      const keys = Object.keys(dirty) as (keyof FormState)[];
      if (keys.length === 0) {
        toast.info("Nothing to save.");
        return;
      }
      // Skip password key when the input is empty — that means "no change"
      // per the PATCH contract. Avoids sending a no-op but-auditable request.
      const ops = keys
        .filter((k) => !(k === "SMTP_PASSWORD_ENC" && form.SMTP_PASSWORD_ENC === ""))
        .map((k) => {
          const val = form[k];
          const str =
            typeof val === "boolean" ? (val ? "true" : "false") : String(val);
          return updateSetting.mutateAsync({ key: k, value: str });
        });
      await Promise.all(ops);
      toast.success("SMTP settings saved.");
      setDirty({});
      // After a successful password save, drop back to the masked state.
      if (form.SMTP_PASSWORD_ENC !== "") {
        setReplacingPassword(false);
        setForm((p) => ({ ...p, SMTP_PASSWORD_ENC: "" }));
        setShowPassword(false);
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Save failed.");
    } finally {
      setSaving(false);
    }
  }

  async function onSendTest() {
    try {
      const res = await sendTest.mutateAsync();
      if (res.ok) {
        toast.success(`Test email sent to ${res.recipients} auditor(s).`);
      } else {
        toast.error(res.error || "Test send failed.");
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Test send failed.");
    }
  }

  if (isLoading) return <Skeleton className="h-96" />;

  const policyNeedsMinScore = form.SMTP_TRIGGER_POLICY === "first_detection_min_score";

  return (
    <Card>
      <CardContent className="space-y-5 p-5">
        {/* Enable toggle — primary control */}
        <div className="flex items-start gap-3">
          <Checkbox
            id="smtp-enabled"
            checked={form.SMTP_ENABLED}
            onCheckedChange={(v) => set("SMTP_ENABLED", !!v)}
            className="mt-1"
          />
          <div>
            <Label htmlFor="smtp-enabled" className="cursor-pointer">
              Enable SMTP notifications
            </Label>
            <p className="text-xs text-muted-foreground">
              When off, alerts still record but no email is sent.
            </p>
          </div>
        </div>

        {/* Connection */}
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div>
            <Label htmlFor="smtp-host">SMTP host</Label>
            <Input
              id="smtp-host"
              value={form.SMTP_HOST}
              onChange={(e) => set("SMTP_HOST", e.target.value)}
              placeholder="smtp.example.com"
              autoComplete="off"
            />
          </div>
          <div>
            <Label htmlFor="smtp-port">Port</Label>
            <Input
              id="smtp-port"
              type="number"
              value={form.SMTP_PORT}
              onChange={(e) => set("SMTP_PORT", e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="smtp-encryption">Encryption</Label>
            <Select
              value={form.SMTP_ENCRYPTION}
              onValueChange={(v) => set("SMTP_ENCRYPTION", v)}
            >
              <SelectTrigger id="smtp-encryption">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="starttls">STARTTLS (587, recommended)</SelectItem>
                <SelectItem value="tls">Implicit TLS / SMTPS (465)</SelectItem>
                <SelectItem value="none">Plaintext (25, test/dev only)</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-center gap-2 pt-6">
            <Checkbox
              id="smtp-tls-verify"
              checked={form.SMTP_TLS_VERIFY}
              onCheckedChange={(v) => set("SMTP_TLS_VERIFY", !!v)}
            />
            <Label htmlFor="smtp-tls-verify" className="cursor-pointer text-sm">
              Verify TLS certificate
            </Label>
          </div>
        </div>

        {/* Auth */}
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div>
            <Label htmlFor="smtp-username">Username</Label>
            <Input
              id="smtp-username"
              value={form.SMTP_USERNAME}
              onChange={(e) => set("SMTP_USERNAME", e.target.value)}
              autoComplete="off"
            />
          </div>
          <div>
            <Label htmlFor="smtp-password">Password</Label>
            <div className="flex gap-2">
              {passwordIsSet && !replacingPassword ? (
                <>
                  <Input
                    id="smtp-password"
                    value="••••••••••••"
                    disabled
                    className="font-mono"
                  />
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setReplacingPassword(true)}
                  >
                    Replace
                  </Button>
                </>
              ) : (
                <>
                  <Input
                    id="smtp-password"
                    type={showPassword ? "text" : "password"}
                    value={form.SMTP_PASSWORD_ENC}
                    onChange={(e) => set("SMTP_PASSWORD_ENC", e.target.value)}
                    autoComplete="new-password"
                    placeholder={passwordIsSet ? "Enter new password" : ""}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    onClick={() => setShowPassword((s) => !s)}
                  >
                    {showPassword ? (
                      <EyeOff className="h-4 w-4" />
                    ) : (
                      <Eye className="h-4 w-4" />
                    )}
                  </Button>
                </>
              )}
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              Stored AES-GCM-256 encrypted. Leave blank to keep the current password.
            </p>
          </div>
        </div>

        {/* Identity */}
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div>
            <Label htmlFor="smtp-from-address">From address</Label>
            <Input
              id="smtp-from-address"
              type="email"
              value={form.SMTP_FROM_ADDRESS}
              onChange={(e) => set("SMTP_FROM_ADDRESS", e.target.value)}
              placeholder="alerts@company.com"
            />
          </div>
          <div>
            <Label htmlFor="smtp-from-name">From display name</Label>
            <Input
              id="smtp-from-name"
              value={form.SMTP_FROM_NAME}
              onChange={(e) => set("SMTP_FROM_NAME", e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="smtp-reply-to">Reply-To (optional)</Label>
            <Input
              id="smtp-reply-to"
              type="email"
              value={form.SMTP_REPLY_TO}
              onChange={(e) => set("SMTP_REPLY_TO", e.target.value)}
              placeholder="security@company.com"
            />
          </div>
          <div>
            <Label htmlFor="smtp-timeout">Timeout (seconds)</Label>
            <Input
              id="smtp-timeout"
              type="number"
              value={form.SMTP_TIMEOUT_SECONDS}
              onChange={(e) => set("SMTP_TIMEOUT_SECONDS", e.target.value)}
            />
          </div>
        </div>

        {/* Trigger policy */}
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div>
            <Label htmlFor="smtp-policy">Trigger policy</Label>
            <Select
              value={form.SMTP_TRIGGER_POLICY}
              onValueChange={(v) => set("SMTP_TRIGGER_POLICY", v)}
            >
              <SelectTrigger id="smtp-policy">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="first_detection">
                  Only on first detection (recommended)
                </SelectItem>
                <SelectItem value="first_detection_min_score">
                  First detection + minimum score
                </SelectItem>
                <SelectItem value="every_scan">
                  Every scan that raises an alert (noisy)
                </SelectItem>
              </SelectContent>
            </Select>
          </div>
          {policyNeedsMinScore ? (
            <div>
              <Label htmlFor="smtp-min-score">Minimum score</Label>
              <Input
                id="smtp-min-score"
                type="number"
                step="0.05"
                min="0"
                max="1"
                value={form.SMTP_MIN_SCORE}
                onChange={(e) => set("SMTP_MIN_SCORE", e.target.value)}
              />
            </div>
          ) : null}
        </div>

        {/* Auditor recipients — read-only info */}
        <div className="rounded-md border border-border bg-muted/30 p-4">
          <div className="mb-1 flex items-center gap-2">
            <UsersIcon className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm font-semibold">Auditor recipients</span>
          </div>
          <p className="text-xs text-muted-foreground">
            <b>{auditorCount}</b>{" "}
            {auditorCount === 1 ? "user with the" : "users with the"}{" "}
            <code className="font-mono text-xs">auditor</code> role will receive
            alert emails.{" "}
            <Link to="/users" className="underline">
              Manage in Users
            </Link>
            .
          </p>
        </div>

        {/* Action row */}
        <div className="flex items-center gap-3 border-t border-border pt-4">
          <Button onClick={onSave} disabled={saving || Object.keys(dirty).length === 0}>
            {saving ? "Saving…" : "Save"}
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={onSendTest}
            disabled={sendTest.isPending || !form.SMTP_ENABLED}
          >
            <Send className="mr-1.5 h-3.5 w-3.5" />
            {sendTest.isPending ? "Sending…" : "Send test email"}
          </Button>
          {!form.SMTP_ENABLED ? (
            <span className="text-xs text-muted-foreground">
              Enable SMTP to allow test sends.
            </span>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
