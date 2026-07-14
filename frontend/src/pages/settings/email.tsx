import { SmtpSettings } from "@/components/shared/smtp-settings";

export default function SettingsEmailPage() {
  return (
    <>
      <h2 className="mb-3 text-sm font-semibold tracking-tight">
        Email notifications
      </h2>
      <p className="mb-3 text-xs text-muted-foreground">
        Send DLP alerts to auditor users by email. Recipients are derived from
        the Users page (role = auditor); there is no separate mailing list.
      </p>
      <SmtpSettings />
    </>
  );
}
