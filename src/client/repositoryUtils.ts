const githubRepoPathPattern =
  /^([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+?)(?:\.git)?$/;

const normalizeGitHubPath = (path: string): string | undefined => {
  const match = path.replace(/^\/+/, "").match(githubRepoPathPattern);
  if (!match) {
    return undefined;
  }
  return `${match[1]}/${match[2]}`;
};

export const githubRemoteToHttpsUrl = (
  remoteUrl: string,
): string | undefined => {
  const trimmed = remoteUrl.trim();
  if (!trimmed) {
    return undefined;
  }

  const scpLikeMatch = trimmed.match(/^git@github\.com:(.+)$/);
  if (scpLikeMatch) {
    const repoPath = normalizeGitHubPath(scpLikeMatch[1]);
    return repoPath ? `https://github.com/${repoPath}` : undefined;
  }

  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    return undefined;
  }

  if (parsed.hostname !== "github.com") {
    return undefined;
  }

  if (parsed.protocol === "ssh:" && parsed.username !== "git") {
    return undefined;
  }

  if (parsed.protocol !== "https:" && parsed.protocol !== "ssh:") {
    return undefined;
  }

  const repoPath = normalizeGitHubPath(parsed.pathname);
  return repoPath ? `https://github.com/${repoPath}` : undefined;
};
