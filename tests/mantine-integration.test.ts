import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const packageJson = JSON.parse(
  readFileSync(join(process.cwd(), "package.json"), "utf8"),
) as {
  dependencies?: Record<string, string>;
};
const clientEntry = readFileSync(
  join(process.cwd(), "src/client/main.tsx"),
  "utf8",
);

describe("Mantine client integration", () => {
  it("declares Mantine packages and wraps the client with MantineProvider", () => {
    expect(packageJson.dependencies).toHaveProperty("@mantine/core");
    expect(packageJson.dependencies).toHaveProperty("@mantine/hooks");
    expect(clientEntry).toContain("@mantine/core/styles.css");
    expect(clientEntry).toContain("MantineProvider");
  });
});
