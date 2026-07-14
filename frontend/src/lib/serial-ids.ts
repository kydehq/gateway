// Serial-ID formatters render a stable monotonic integer (from a Postgres
// IDENTITY column on the backend) as a zero-padded display string like
// `ALT-0042`. They expect an integer or integer-like string — passing a UUID
// silently produces an unusable display value, so always read the dedicated
// `serial_id` field on the API response rather than the entity's primary key.

const SERIAL_PAD = 4;

const pad = (n: number | string) => String(n).padStart(SERIAL_PAD, "0");

// Renders `prefix-####` when the serial is present; otherwise renders the
// prefix with a `?` so missing data is visible at a glance rather than
// silently dropping the badge.
const formatSerial = (
  prefix: string,
  serial: number | string | null | undefined,
): string => {
  if (serial === null || serial === undefined || serial === "") {
    return `${prefix}-????`;
  }
  return `${prefix}-${pad(serial)}`;
};

export const formatAlertId    = (serial: number | string | null | undefined) => formatSerial("ALT", serial);
export const formatChainId    = (serial: number | string | null | undefined) => formatSerial("CHAIN", serial);
export const formatSessionId  = (serial: number | string | null | undefined) => formatSerial("SES", serial);
export const formatIncidentId = (serial: number | string | null | undefined) => formatSerial("INC", serial);
export const formatSeqId      = (seq: number | string | null | undefined) =>
  seq === null || seq === undefined || seq === "" ? "SEQ-?" : `SEQ-${seq}`;
