import { describe, expect, it } from "vitest";

import {
  githubRemoteToHttpsUrl,
  githubRepositoryLink,
} from "../src/client/repositoryUtils.js";

describe("repository utilities", () => {
  it("converts GitHub SSH remotes to browser URLs", () => {
    expect(
      githubRemoteToHttpsUrl("git@github.com:vibepod/vibepod-cli.git"),
    ).toBe("https://github.com/vibepod/vibepod-cli");
    expect(
      githubRemoteToHttpsUrl("ssh://git@github.com/vibepod/vibepod-board.git"),
    ).toBe("https://github.com/vibepod/vibepod-board");
  });

  it("normalizes GitHub HTTPS remotes and ignores non-GitHub remotes", () => {
    expect(
      githubRemoteToHttpsUrl("https://github.com/vibepod/vibepod-cli.git"),
    ).toBe("https://github.com/vibepod/vibepod-cli");
    expect(
      githubRemoteToHttpsUrl("git@gitlab.com:vibepod/vibepod-cli.git"),
    ).toBeUndefined();
    expect(githubRemoteToHttpsUrl("")).toBeUndefined();
  });

  it("names the GitHub repository a remote points at", () => {
    expect(
      githubRepositoryLink("git@github.com:vibepod/vibepod-board.git"),
    ).toEqual({
      url: "https://github.com/vibepod/vibepod-board",
      name: "vibepod/vibepod-board",
    });
    expect(
      githubRepositoryLink("git@gitlab.com:vibepod/vibepod-board.git"),
    ).toBeUndefined();
    expect(githubRepositoryLink(undefined)).toBeUndefined();
  });
});
