import {
  Badge,
  Box,
  Button,
  Group,
  SegmentedControl,
  Stack,
  Text,
} from "@mantine/core";
import type {
  PermissionArea,
  PermissionLevel,
  TokenPermissions,
} from "../shared/types.js";
import {
  fullAccess,
  permissionLevelLabels,
  readOnly,
  summarizePermissions,
} from "./tokenPermissions.js";

/** One row per area with the levels it offers, and presets for the common sets. */
export const TokenPermissionsEditor = ({
  areas,
  value,
  onChange,
}: {
  areas: PermissionArea[];
  value: TokenPermissions;
  onChange: (permissions: TokenPermissions) => void;
}) => (
  <Stack gap="xs">
    <Group justify="space-between" align="center">
      <Box>
        <Text size="sm" fw={500}>
          Permissions
        </Text>
        <Text size="xs" c="dimmed">
          What the token may read or change, the same in each of its projects.
        </Text>
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
          aria-label={area.label}
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

/** A token's permissions as badges. Renders nothing until the areas are known. */
export const TokenPermissionsSummary = ({
  areas,
  permissions,
}: {
  areas: PermissionArea[];
  permissions: TokenPermissions;
}) =>
  areas.length === 0 ? null : (
    <Stack gap={4}>
      <Text size="xs" fw={700} tt="uppercase" c="dimmed">
        Permissions
      </Text>
      <Group gap={6}>
        {summarizePermissions(areas, permissions).map((label) => (
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
    </Stack>
  );
