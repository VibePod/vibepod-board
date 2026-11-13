import type { Project } from "../shared/types.js";
import { activeProjects } from "./archivedProjects.js";
import type { NavigationState, NavigationView } from "./navigation.js";

export const navigationForProjectSelection = (
  currentView: NavigationView,
  selectedProjectId: string,
): NavigationState => ({
  activeView:
    currentView === "projects" || currentView === "archivedProjects"
      ? "ideas"
      : currentView,
  selectedProjectId,
});

export const shouldShowProjectSidebar = (
  activeView: NavigationView,
  hasSelectedProject: boolean,
): boolean =>
  activeView !== "projects" &&
  activeView !== "archivedProjects" &&
  hasSelectedProject;

/** Archived projects are left out: they are opened from Archived projects. */
export const projectSelectorOptions = (projects: Project[]) =>
  activeProjects(projects).map((project) => ({
    value: project.id,
    label: `${project.key} - ${project.title}`,
  }));
