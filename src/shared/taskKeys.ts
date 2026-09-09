/**
 * A caller-supplied reference to a task. `VP-236` and a bare task number are
 * resolved against the database; anything else is treated as an opaque id.
 */
export type TaskReference =
  | { kind: "id" }
  | { kind: "key"; projectKey: string; taskNumber: number }
  | { kind: "number"; taskNumber: number };

const keyPattern = /^([A-Za-z]{1,3})-([1-9][0-9]*)$/;
const numberPattern = /^([1-9][0-9]*)$/;

/**
 * Task numbers start at 1, so `VP-0` and `VP-01` are typos rather than keys and
 * fall through to an id lookup instead of resolving to something unintended.
 */
export const parseTaskReference = (reference: string): TaskReference => {
  const value = reference.trim();

  const keyMatch = keyPattern.exec(value);
  if (keyMatch) {
    return {
      kind: "key",
      projectKey: keyMatch[1].toUpperCase(),
      taskNumber: Number(keyMatch[2]),
    };
  }

  const numberMatch = numberPattern.exec(value);
  if (numberMatch) {
    return { kind: "number", taskNumber: Number(numberMatch[1]) };
  }

  return { kind: "id" };
};
