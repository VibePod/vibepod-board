import {
  Badge,
  Box,
  Button,
  Group,
  MultiSelect,
  SegmentedControl,
  Stack,
  Switch,
  Text,
} from "@mantine/core";
import type {
  ApiTokenProject,
  PermissionArea,
  PermissionLevel,
  TokenPermissions,
} from "../shared/types.js";
import {
  fullAccess,
  permissionLevelLabels,
  permissionsFor,
  projectPermissionsFor,
  readOnly,
  summarizeProjectPermissions,
  type TokenAccessDraft,
  withIndividual,
  withProjects,
} from "./tokenPermissions.js";

/**
 * One row per area with the levels it offers, and presets for the common sets. `scope`
 * names the project the set is for when each project has its own.
 */
export const TokenPermissionsEditor = ({
  areas,
  value,
  onChange,
  scope,
}: {
  areas: PermissionArea[];
  value: TokenPermissions;
  onChange: (permissions: TokenPermissions) => void;
  scope?: string;
}) => (
  <Stack gap="xs">
    <Group justify="space-between" align="center">
      <Box>
        <Text size="sm" fw={500}>
          {scope ?? "Permissions"}
        </Text>
        {!scope && (
          <Text size="xs" c="dimmed">
            What the token may read or change.
          </Text>
        )}
      </Box>
      <Group gap="xs">
        <Button
          type="button"
          size="compact-xs"
          variant="default"
          onClick={() => onChange(fullAccess(areas))}
        >
          Full access
        </Button>
        <Button
          type="button"
          size="compact-xs"
          variant="default"
          onClick={() => onChange(readOnly(areas))}
        >
          Read only
        </Button>
      </Group>
    </Group>
    {areas.map((area) => (
      <Group
        key={area.key}
        justify="space-between"
        align="center"
        wrap="nowrap"
        gap="md"
      >
        <Box>
          <Text size="sm" fw={500}>
            {area.label}
          </Text>
          <Text size="xs" c="dimmed">
            {area.description}
          </Text>
        </Box>
        <SegmentedControl
          aria-label={scope ? `${area.label} in ${scope}` : area.label}
          size="xs"
          style={{ flexShrink: 0 }}
          value={value[area.key] ?? "none"}
          onChange={(level) =>
            onChange({ ...value, [area.key]: level as PermissionLevel })
          }
          data={area.levels.map((level) => ({
            value: level,
            label: permissionLevelLabels[level],
          }))}
        />
      </Group>
    ))}
  </Stack>
);

/**
 * The projects of a token and what it may do in them: one set for all projects, or, from
 * two projects on, a set for each.
 */
export const TokenAccessFields = ({
  areas,
  projectOptions,
  draft,
  onChange,
}: {
  areas: PermissionArea[];
  projectOptions: { value: string; label: string }[];
  draft: TokenAccessDraft;
  onChange: (draft: TokenAccessDraft) => void;
}) => {
  const projectLabel = (projectId: string) =>
    projectOptions.find((option) => option.value === projectId)?.label ??
    projectId;
  return (
    <Stack gap="md">
      <MultiSelect
        label="Projects"
        value={draft.projectIds}
        onChange={(projectIds) =>
          onChange(withProjects(areas, draft, projectIds))
        }
        data={projectOptions}
        searchable
        required
      />
      {areas.length > 0 && draft.projectIds.length > 1 && (
        <Switch
          label="Set permissions for each project"
          checked={draft.individual}
          onChange={(event) =>
            onChange(withIndividual(areas, draft, event.currentTarget.checked))
          }
        />
      )}
      {areas.length > 0 &&
        (draft.individual ? (
          draft.projectIds.map((projectId) => (
            <TokenPermissionsEditor
              key={projectId}
              areas={areas}
              scope={projectLabel(projectId)}
              value={projectPermissionsFor(areas, draft, projectId)}
              onChange={(permissions) =>
                onChange({
                  ...draft,
                  byProject: { ...draft.byProject, [projectId]: permissions },
                })
              }
            />
          ))
        ) : (
          <TokenPermissionsEditor
            areas={areas}
            value={permissionsFor(areas, draft.shared)}
            onChange={(shared) => onChange({ ...draft, shared })}
          />
        ))}
    </Stack>
  );
};

const PermissionBadges = ({ labels }: { labels: string[] }) => (
  <Group gap={6}>
    {labels.map((label) => (
      <Badge
        key={label}
        variant="light"
        color={label === "No access" ? "orange" : "gray"}
        tt="none"
      >
        {label}
      </Badge>
    ))}
  </Group>
);

/**
 * A token's permissions as badges: once when its projects agree, otherwise project by
 * project. Renders nothing until the areas are known.
 */
export const TokenPermissionsSummary = ({
  areas,
  projects,
}: {
  areas: PermissionArea[];
  projects: ApiTokenProject[];
}) => {
  const rows = summarizeProjectPermissions(areas, projects);
  if (areas.length === 0 || rows.length === 0) {
    return null;
  }
  return (
    <Stack gap={4}>
      <Text size="xs" fw={700} tt="uppercase" c="dimmed">
        Permissions
      </Text>
      {rows.length === 1 && !rows[0].project ? (
        <PermissionBadges labels={rows[0].badges} />
      ) : (
        <Stack
          component="ul"
          aria-label="Permissions by project"
          gap={4}
          m={0}
          p={0}
          style={{ listStyle: "none" }}
        >
          {rows.map((row) => (
            <Group component="li" key={row.project} gap={8}>
              <Text size="sm">{row.project}</Text>
              <PermissionBadges labels={row.badges} />
            </Group>
          ))}
        </Stack>
      )}
    </Stack>
  );
};
