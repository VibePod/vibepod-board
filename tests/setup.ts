import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Testing Library only registers this itself when test globals are enabled. Without it a
// test leaves its React tree mounted, and pending component timers (Mantine's modal scroll
// lock, for one) can fire after the jsdom environment is gone.
afterEach(() => {
  cleanup();
});
