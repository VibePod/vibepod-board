import { parseProjectBundle } from "../shared/projectBundle.js";
import type { Project, ProjectBundle } from "../shared/types.js";

export const parseProjectBundleText = (text: string): ProjectBundle => {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new Error("Project file is not valid JSON");
  }

  try {
    return parseProjectBundle(parsed);
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Validation failed";
    throw new Error(`Project file is invalid: ${message}`);
  }
};

export const projectBundlePreview = (
  bundle: ProjectBundle,
  projects: Project[],
) => ({
  key: bundle.project.key,
  title: bundle.project.title,
  tasks: bundle.ideas.length,
  cards: bundle.boardCards.length,
  documents: bundle.documents.length,
  existingProjectId: projects.find(
    (project) => project.key === bundle.project.key,
  )?.id,
});

export const projectExportFileName = (project: Project) =>
  `${project.key}-project.json`;

export const downloadResponse = async (
  response: Response,
  filename: string,
): Promise<void> => {
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  try {
    anchor.href = url;
    anchor.download = filename;
    document.body.append(anchor);
    anchor.click();
  } finally {
    anchor.remove();
    URL.revokeObjectURL(url);
  }
};
