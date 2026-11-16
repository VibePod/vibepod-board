import { Alert, Button, Stack, TextInput } from "@mantine/core";
import { KeyRound } from "lucide-react";
import { type FormEvent, useState } from "react";
import { api } from "./api.js";

/** The server asks for the same length. */
export const MIN_PASSWORD_LENGTH = 12;

/** What is wrong with a new password before it is sent, or null. */
export const passwordProblem = (
  next: string,
  repeat: string,
): string | null => {
  if (next.length < MIN_PASSWORD_LENGTH) {
    return `The new password needs at least ${MIN_PASSWORD_LENGTH} characters.`;
  }
  if (next !== repeat) {
    return "The two new passwords differ.";
  }
  return null;
};

/**
 * A user sets their own password: the current one, and the new one twice. Used after
 * signing in with a temporary password and for a change later on. `onChanged` runs once
 * the server accepted the new password.
 */
export const PasswordChangeForm = ({
  onChanged,
}: {
  onChanged: () => void;
}) => {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [error, setError] = useState("");
  const [isSaving, setIsSaving] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const problem = passwordProblem(next, repeat);
    if (problem) {
      setError(problem);
      return;
    }
    setError("");
    setIsSaving(true);
    try {
      await api("/api/auth/password", {
        method: "POST",
        body: JSON.stringify({ currentPassword: current, newPassword: next }),
      });
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to change the password",
      );
      setIsSaving(false);
      return;
    }
    // The form is taken away by whoever shows it; nothing is set after this.
    onChanged();
  };

  return (
    <form className="modal-form" onSubmit={(event) => void submit(event)}>
      <Stack gap="md">
        {error && (
          <Alert color="red" variant="light">
            {error}
          </Alert>
        )}
        <TextInput
          label="Current password"
          aria-label="Current password"
          type="password"
          autoComplete="current-password"
          value={current}
          onChange={(event) => setCurrent(event.currentTarget.value)}
          required
        />
        <TextInput
          label="New password"
          aria-label="New password"
          description={`At least ${MIN_PASSWORD_LENGTH} characters`}
          type="password"
          autoComplete="new-password"
          value={next}
          onChange={(event) => setNext(event.currentTarget.value)}
          required
        />
        <TextInput
          label="Repeat new password"
          aria-label="Repeat new password"
          type="password"
          autoComplete="new-password"
          value={repeat}
          onChange={(event) => setRepeat(event.currentTarget.value)}
          required
        />
        <Button
          type="submit"
          loading={isSaving}
          leftSection={<KeyRound size={16} />}
        >
          Set Password
        </Button>
      </Stack>
    </form>
  );
};
