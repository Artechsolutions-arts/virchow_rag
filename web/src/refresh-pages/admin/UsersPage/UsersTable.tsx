"use client";

import { useMemo, useState } from "react";
import DataTable from "@/refresh-components/table/DataTable";
import { createTableColumns } from "@/refresh-components/table/columns";
import { Content } from "@opal/layouts";
import { Button } from "@opal/components";
import { SvgDownload } from "@opal/icons";
import SvgNoResult from "@opal/illustrations/no-result";
import { IllustrationContent } from "@opal/layouts";
import SimpleLoader from "@/refresh-components/loaders/SimpleLoader";
import { UserRole, UserStatus, USER_STATUS_LABELS } from "@/lib/types";
import { timeAgo } from "@/lib/time";
import Text from "@/refresh-components/texts/Text";
import InputTypeIn from "@/refresh-components/inputs/InputTypeIn";
import { toast } from "@/hooks/useToast";
import useAdminUsers from "@/hooks/useAdminUsers";
import useGroups from "@/hooks/useGroups";
import { downloadUsersCsv } from "./svc";
import UserFilters from "./UserFilters";
import UserRowActions from "./UserRowActions";
import UserRoleCell from "./UserRoleCell";
import type {
  UserRow,
  GroupOption,
  StatusFilter,
  StatusCountMap,
} from "./interfaces";
import { getInitials } from "./utils";

// ---------------------------------------------------------------------------
// Column renderers
// ---------------------------------------------------------------------------

function renderNameColumn(email: string, row: UserRow) {
  return (
    <Content
      sizePreset="main-ui"
      variant="section"
      title={row.personal_name ?? email}
      description={row.personal_name ? email : undefined}
    />
  );
}

function renderStatusColumn(value: string, row: UserRow) {
  const label = USER_STATUS_LABELS[value as UserStatus] || value || "Unknown";
  return (
    <div className="flex flex-col">
      <Text as="span" mainUiBody text03 nowrap>
        {label}
      </Text>
      {row.is_scim_synced && (
        <Text as="span" secondaryBody text03>
          SCIM synced
        </Text>
      )}
    </div>
  );
}

function renderLastUpdatedColumn(value: string | null) {
  return (
    <Text as="span" secondaryBody text03>
      {value ? timeAgo(value) ?? "—" : "—"}
    </Text>
  );
}

// ---------------------------------------------------------------------------
// Columns
// ---------------------------------------------------------------------------

const tc = createTableColumns<UserRow>();

function buildColumns(onMutate: () => void) {
  return [
    tc.qualifier({
      content: "avatar-user",
      getInitials: (row) => getInitials(row.personal_name, row.email),
      selectable: false,
    }),
    tc.column("email", {
      header: "Name",
      weight: 22,
      minWidth: 140,
      cell: renderNameColumn,
    }),
    tc.column("role", {
      header: "Account Type",
      weight: 10,
      minWidth: 90,
      cell: (_value, row) => <UserRoleCell user={row} onMutate={onMutate} />,
    }),
    tc.column("department", {
      header: "Department",
      weight: 10,
      minWidth: 90,
      cell: (value, _row) => (
        <Text as="span" mainUiBody text03 className="whitespace-nowrap">
          {value || "Unknown"}
        </Text>
      ),
    }),
    tc.column("company", {
      header: "Company",
      weight: 10,
      minWidth: 80,
      cell: (value, _row) => (
        <Text as="span" mainUiBody text03 className="whitespace-nowrap">
          {value || "Unknown"}
        </Text>
      ),
    }),
    tc.column("status", {
      header: "Status",
      weight: 10,
      minWidth: 80,
      cell: renderStatusColumn,
    }),
    tc.column("created_at", {
      header: "Joined",
      weight: 8,
      minWidth: 75,
      cell: (value) => (
        <Text as="span" mainUiBody text02>
          {value ? timeAgo(value as string) : "—"}
        </Text>
      ),
    }),
    tc.column("updated_at", {
      header: "Activity",
      weight: 8,
      minWidth: 75,
      cell: renderLastUpdatedColumn,
    }),
    tc.actions({
      cell: (row) => <UserRowActions user={row} onMutate={onMutate} />,
    }),
  ];
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const PAGE_SIZE = 8;

interface UsersTableProps {
  selectedStatuses: StatusFilter;
  onStatusesChange: (statuses: StatusFilter) => void;
  roleCounts: Record<string, number>;
  statusCounts: StatusCountMap;
}

export default function UsersTable({
  selectedStatuses,
  onStatusesChange,
  roleCounts,
  statusCounts,
}: UsersTableProps) {
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedRoles, setSelectedRoles] = useState<UserRole[]>([]);
  const [selectedGroups, setSelectedGroups] = useState<number[]>([]);

  const { data: allGroups } = useGroups();

  const groupOptions: GroupOption[] = useMemo(
    () =>
      (allGroups ?? []).map((g) => ({
        id: g.id,
        name: g.name,
        memberCount: g.users.length,
      })),
    [allGroups]
  );

  const { users, isLoading, error, refresh } = useAdminUsers();

  const columns = useMemo(() => buildColumns(refresh), [refresh]);

  // Client-side filtering
  const filteredUsers = useMemo(() => {
    let result = users;

    if (selectedRoles.length > 0) {
      result = result.filter(
        (u) => u.role !== null && selectedRoles.includes(u.role)
      );
    }

    if (selectedStatuses.length > 0) {
      result = result.filter((u) => selectedStatuses.includes(u.status));
    }

    if (selectedGroups.length > 0) {
      result = result.filter((u) =>
        u.groups.some((g) => selectedGroups.includes(g.id))
      );
    }

    return result;
  }, [users, selectedRoles, selectedStatuses, selectedGroups]);

  if (isLoading) {
    return (
      <div className="flex justify-center py-12">
        <SimpleLoader className="h-6 w-6" />
      </div>
    );
  }

  if (error) {
    return (
      <Text as="p" secondaryBody text03>
        Failed to load users. Please try refreshing the page.
      </Text>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <InputTypeIn
        value={searchTerm}
        onChange={(e) => setSearchTerm(e.target.value)}
        placeholder="Search users..."
        leftSearchIcon
      />
      <UserFilters
        selectedRoles={selectedRoles}
        onRolesChange={setSelectedRoles}
        selectedGroups={selectedGroups}
        onGroupsChange={setSelectedGroups}
        groups={groupOptions}
        selectedStatuses={selectedStatuses}
        onStatusesChange={onStatusesChange}
        roleCounts={roleCounts}
        statusCounts={statusCounts}
      />
      <DataTable
        data={filteredUsers}
        columns={columns}
        getRowId={(row) => row.id ?? row.email}
        pageSize={PAGE_SIZE}
        searchTerm={searchTerm}
        initialColumnVisibility={{
          email: true,
          role: true,
          department: true,
          company: true,
          status: true,
          created_at: true,
          updated_at: true,
          actions: true,
        }}
        emptyState={
          <IllustrationContent
            illustration={SvgNoResult}
            title="No users found"
            description="No users match the current filters."
          />
        }
        footer={{
          mode: "summary",
          leftExtra: (
            <Button
              icon={SvgDownload}
              prominence="tertiary"
              size="sm"
              tooltip="Download CSV"
              aria-label="Download CSV"
              onClick={() => {
                downloadUsersCsv().catch((err) => {
                  toast.error(
                    err instanceof Error
                      ? err.message
                      : "Failed to download CSV"
                  );
                });
              }}
            />
          ),
        }}
      />
    </div>
  );
}
