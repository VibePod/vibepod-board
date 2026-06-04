import type { Project } from "../shared/types.js";
import type { NavigationState, NavigationView } from "./navigation.js";

export const navigationForProjectSelection = (
  currentView: NavigationView,
  selectedProjectId: string
): NavigationState => ({
  activeView: currentView === "projects" ? "ideas" : currentView,
  selectedProjectId
});

export const shouldShowProjectSidebar = (
  activeView: NavigationView,
  hasSelectedProject: boolean
): boolean => activeView !== "projects" && hasSelectedProject;

export const projectSelectorOptions = (projects: Project[]) =>
  projects.map((project) => ({
    value: project.id,
    label: `${project.key} - ${project.title}`
  }));
