export const navigationViews = ["projects", "ideas", "board", "documents"] as const;

export type NavigationView = (typeof navigationViews)[number];

export type NavigationState = {
  activeView: NavigationView;
  selectedProjectId: string;
};

const projectSections: Record<Exclude<NavigationView, "projects">, string> = {
  ideas: "tasks",
  board: "board",
  documents: "notes"
};

const sectionViews: Record<string, Exclude<NavigationView, "projects">> = {
  tasks: "ideas",
  board: "board",
  notes: "documents"
};

export const parseNavigationPath = (pathname: string): NavigationState => {
  const parts = pathname
    .split("/")
    .filter(Boolean)
    .map((part) => safeDecode(part));

  if (parts[0] !== "projects" || !parts[1]) {
    return { activeView: "projects", selectedProjectId: "" };
  }

  const section = parts[2] ?? "tasks";
  return {
    activeView: sectionViews[section] ?? "ideas",
    selectedProjectId: parts[1]
  };
};

export const formatNavigationPath = ({ activeView, selectedProjectId }: NavigationState): string => {
  if (activeView === "projects" || !selectedProjectId) {
    return "/projects";
  }

  return `/projects/${encodeURIComponent(selectedProjectId)}/${projectSections[activeView]}`;
};

const safeDecode = (value: string): string => {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
};
