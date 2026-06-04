"use client";

import { useState, useCallback } from "react";
import { SvgUser, SvgUserPlus } from "@opal/icons";
import { Button } from "@opal/components";
import * as SettingsLayouts from "@/layouts/settings-layouts";
import { useScimToken } from "@/hooks/useScimToken";
import { usePaidEnterpriseFeaturesEnabled } from "@/components/settings/usePaidEnterpriseFeaturesEnabled";
import useUserCounts from "@/hooks/useUserCounts";
import { UserStatus } from "@/lib/types";
import type { StatusFilter, StatusCountMap } from "./UsersPage/interfaces";

import UsersSummary from "./UsersPage/UsersSummary";
import UsersTable from "./UsersPage/UsersTable";
import AddUserModal from "./UsersPage/AddUserModal";

// ---------------------------------------------------------------------------
// Users page content
// ---------------------------------------------------------------------------

interface UsersContentProps {
  activeCount: number | null;
  pendingCount: number | null;
  roleCounts: Record<string, number>;
  statusCounts: StatusCountMap;
  tableKey: number;
}

function UsersContent({
  activeCount,
  pendingCount,
  roleCounts,
  statusCounts,
  tableKey,
}: UsersContentProps) {
  const isEe = usePaidEnterpriseFeaturesEnabled();

  const { data: scimToken } = useScimToken();
  const showScim = isEe && !!scimToken;

  const [selectedStatuses, setSelectedStatuses] = useState<StatusFilter>([]);

  const toggleStatus = (target: UserStatus) => {
    setSelectedStatuses((prev) =>
      prev.includes(target)
        ? prev.filter((s) => s !== target)
        : [...prev, target]
    );
  };

  return (
    <>
      <UsersSummary
        activeUsers={activeCount}
        requests={pendingCount}
        showScim={showScim}
        onFilterActive={() => toggleStatus(UserStatus.ACTIVE)}
        onFilterRequests={() => toggleStatus(UserStatus.REQUESTED)}
      />

      <UsersTable
        key={tableKey}
        selectedStatuses={selectedStatuses}
        onStatusesChange={setSelectedStatuses}
        roleCounts={roleCounts}
        statusCounts={statusCounts}
      />
    </>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function UsersPage() {
  const [inviteOpen, setInviteOpen] = useState(false);
  const [tableKey, setTableKey] = useState(0);

  const { activeCount, pendingCount, roleCounts, statusCounts, refreshCounts } =
    useUserCounts();

  const handleUserAdded = useCallback(() => {
    refreshCounts();
    setTableKey((k) => k + 1);
  }, [refreshCounts]);

  return (
    <SettingsLayouts.Root width="lg">
      <SettingsLayouts.Header
        title="Users & Requests"
        icon={SvgUser}
        rightChildren={
          <Button icon={SvgUserPlus} onClick={() => setInviteOpen(true)}>
            Add User
          </Button>
        }
      />
      <SettingsLayouts.Body>
        <UsersContent
          activeCount={activeCount}
          pendingCount={pendingCount}
          roleCounts={roleCounts}
          statusCounts={statusCounts}
          tableKey={tableKey}
        />
      </SettingsLayouts.Body>

      <AddUserModal
        open={inviteOpen}
        onOpenChange={setInviteOpen}
        onMutate={handleUserAdded}
      />
    </SettingsLayouts.Root>
  );
}
