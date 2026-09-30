import { ActionIcon, Badge, Paper, Text, Tooltip } from "@mantine/core";
import { useMediaQuery } from "@mantine/hooks";
import {
  Archive,
  Columns3,
  FileText,
  ListChecks,
  type LucideIcon,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { Project } from "../shared/types.js";
import {
  formatNavigationPath,
  type NavigationState,
  type NavigationView,
} from "./navigation.js";
import {
  isSidebarShortcut,
  narrowSidebarQuery,
  readSidebarCollapsed,
  writeSidebarCollapsed,
} from "./sidebarState.js";

const navItems: {
  view: Exclude<NavigationView, "projects">;
  label: string;
  icon: LucideIcon;
}[] = [
  { view: "ideas", label: "Tasks", icon: ListChecks },
  { view: "board", label: "Board", icon: Columns3 },
  { view: "archive", label: "Archive", icon: Archive },
  { view: "documents", label: "Notes", icon: FileText },
];

const panelId = "sidebar-panel";

export const Sidebar = ({
  activeView,
  selectedProject,
  navigateTo,
}: {
  activeView: NavigationView;
  selectedProject: Project | null;
  navigateTo: (navigation: NavigationState) => void;
}) => {
  // Read synchronously so the first render, and with it the first paint, already
  // has the remembered width.
  const [collapsed, setCollapsed] = useState(readSidebarCollapsed);
  const [overlayOpen, setOverlayOpen] = useState(false);
  const isNarrow = useMediaQuery(narrowSidebarQuery, false, {
    getInitialValueInEffect: false,
  });
  // On narrow screens the sidebar stacks, so the toggle opens an overlay instead
  // and the remembered rail choice does not apply.
  const isRail = collapsed && !isNarrow;
  const expanded = isNarrow ? overlayOpen : !collapsed;

  const toggle = () => {
    if (isNarrow) {
      setOverlayOpen((current) => !current);
      return;
    }
    setCollapsed((current) => {
      writeSidebarCollapsed(!current);
      return !current;
    });
  };
  const toggleRef = useRef(toggle);
  toggleRef.current = toggle;

  useEffect(() => {
    const onKeydown = (event: KeyboardEvent) => {
      if (isSidebarShortcut(event)) {
        event.preventDefault();
        toggleRef.current();
      }
    };
    window.addEventListener("keydown", onKeydown);
    return () => window.removeEventListener("keydown", onKeydown);
  }, []);

  useEffect(() => {
    if (!isNarrow) {
      setOverlayOpen(false);
    }
  }, [isNarrow]);

  useEffect(() => {
    if (!overlayOpen) {
      return;
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOverlayOpen(false);
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [overlayOpen]);

  const toggleLabel = isNarrow
    ? overlayOpen
      ? "Hide menu"
      : "Show menu"
    : collapsed
      ? "Expand sidebar"
      : "Collapse sidebar";
  const ToggleIcon = isNarrow
    ? overlayOpen
      ? X
      : Menu
    : collapsed
      ? PanelLeftOpen
      : PanelLeftClose;

  const classNames = ["sidebar"];
  if (isRail) classNames.push("is-collapsed");
  if (isNarrow) classNames.push("is-narrow");
  if (overlayOpen) classNames.push("is-open");

  return (
    <aside className={classNames.join(" ")}>
      <div className="sidebar-toolbar">
        <Tooltip
          label={`${toggleLabel} ([)`}
          position="right"
          withArrow
          openDelay={300}
        >
          <ActionIcon
            className="sidebar-toggle"
            variant="subtle"
            color="gray"
            size="lg"
            aria-label={toggleLabel}
            aria-expanded={expanded}
            aria-controls={panelId}
            onClick={toggle}
          >
            <ToggleIcon size={18} />
          </ActionIcon>
        </Tooltip>
      </div>
      <div className="sidebar-panel" id={panelId}>
        <nav className="nav" aria-label="Project">
          {navItems.map(({ view, label, icon: Icon }) => (
            <Tooltip
              key={view}
              label={label}
              position="right"
              withArrow
              disabled={!isRail}
            >
              <a
                className={activeView === view ? "active" : ""}
                aria-current={activeView === view ? "page" : undefined}
                aria-label={isRail ? label : undefined}
                href={
                  selectedProject
                    ? formatNavigationPath({
                        activeView: view,
                        selectedProjectId: selectedProject.id,
                      })
                    : formatNavigationPath({
                        activeView: "projects",
                        selectedProjectId: "",
                      })
                }
                aria-disabled={!selectedProject}
                onClick={(event) => {
                  event.preventDefault();
                  if (selectedProject) {
                    setOverlayOpen(false);
                    navigateTo({
                      activeView: view,
                      selectedProjectId: selectedProject.id,
                    });
                  }
                }}
              >
                <Icon size={18} aria-hidden />
                {!isRail && label}
              </a>
            </Tooltip>
          ))}
        </nav>
        {selectedProject &&
          (isRail ? (
            <Tooltip label={selectedProject.title} position="right" withArrow>
              <Badge
                className="project-context-key"
                variant="filled"
                radius="sm"
                size="lg"
                tabIndex={0}
                aria-label={`Current project: ${selectedProject.title}`}
              >
                {selectedProject.key}
              </Badge>
            </Tooltip>
          ) : (
            <Paper className="project-context" withBorder radius="md" p="sm">
              <Text size="xs" fw={700} tt="uppercase">
                Current Project
              </Text>
              <Text fw={700}>{selectedProject.title}</Text>
            </Paper>
          ))}
      </div>
    </aside>
  );
};
