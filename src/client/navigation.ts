export const navigationViews = [
  "projects",
  "ideas",
  "board",
  "archive",
  "documents",
  "archivedProjects",
] as const;

export type NavigationView = (typeof navigationViews)[number];

export type NavigationState = {
  activeView: NavigationView;
  selectedProjectId: string;
};

/** Top-level views that are not a section of an open project. */
type TopLevelView = "projects" | "archivedProjects";

const archivedProjectsPath = "archived-projects";

const projectSections: Record<Exclude<NavigationView, TopLevelView>, string> = {
  ideas: "tasks",
  board: "board",
  archive: "archive",
  documents: "notes",
};

const sectionViews: Record<string, Exclude<NavigationView, TopLevelView>> = {
  tasks: "ideas",
  board: "board",
  archive: "archive",
  notes: "documents",
};

export const parseNavigationPath = (pathname: string): NavigationState => {
  const parts = pathname
    .split("/")
    .filter(Boolean)
    .map((part) => safeDecode(part));

  // The archived projects list, or one archived project opened read-only.
  if (parts[0] === archivedProjectsPath) {
    return {
      activeView: "archivedProjects",
      selectedProjectId: parts[1] ?? "",
    };
  }

  if (parts[0] !== "projects" || !parts[1]) {
    return { activeView: "projects", selectedProjectId: "" };
  }

  const section = parts[2] ?? "tasks";
  return {
    activeView: sectionViews[section] ?? "ideas",
    selectedProjectId: parts[1],
  };
};

export const formatNavigationPath = ({
  activeView,
  selectedProjectId,
}: NavigationState): string => {
  if (activeView === "archivedProjects") {
    return selectedProjectId
      ? `/${archivedProjectsPath}/${encodeURIComponent(selectedProjectId)}`
      : `/${archivedProjectsPath}`;
  }
  if (activeView === "projects" || !selectedProjectId) {
    return "/projects";
  }

  return `/projects/${encodeURIComponent(selectedProjectId)}/${projectSections[activeView]}`;
};

/** The board search lives in the `q` query parameter so a filtered board is linkable. */
export const parseBoardSearch = (search: string): string =>
  new URLSearchParams(search).get("q") ?? "";

export const formatNavigationUrl = (
  navigation: NavigationState,
  boardSearch = "",
): string => {
  const path = formatNavigationPath(navigation);
  if (navigation.activeView !== "board" || !boardSearch.trim()) {
    return path;
  }
  return `${path}?${new URLSearchParams({ q: boardSearch })}`;
};

const safeDecode = (value: string): string => {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
};
