"use client";

import { useState } from "react";
import { Button } from "@opal/components";
import {
  SvgMoreHorizontal,
  SvgEdit,
  SvgXCircle,
  SvgUserCheck,
  SvgUserPlus,
  SvgUserX,
  SvgKey,
} from "@opal/icons";
import { Disabled } from "@opal/core";
import LineItem from "@/refresh-components/buttons/LineItem";
import Popover from "@/refresh-components/Popover";
import Separator from "@/refresh-components/Separator";
import { Section } from "@/layouts/general-layouts";
import Text from "@/refresh-components/texts/Text";
import { UserStatus } from "@/lib/types";
import { toast } from "@/hooks/useToast";
import { approveRequest } from "./svc";
import EditUserModal from "./EditUserModal";
import {
  CancelInviteModal,
  DeactivateUserModal,
  ActivateUserModal,
  DeleteUserModal,
  ResetPasswordModal,
} from "./UserActionModals";
import type { UserRow } from "./interfaces";

enum Modal {
  DEACTIVATE = "deactivate",
  ACTIVATE = "activate",
  DELETE = "delete",
  CANCEL_INVITE = "cancelInvite",
  EDIT_USER = "editUser",
  RESET_PASSWORD = "resetPassword",
}

interface UserRowActionsProps {
  user: UserRow;
  onMutate: () => void;
}

export default function UserRowActions({ user, onMutate }: UserRowActionsProps) {
  const [modal, setModal] = useState<Modal | null>(null);
  const [popoverOpen, setPopoverOpen] = useState(false);

  const openModal = (type: Modal) => {
    setPopoverOpen(false);
    setModal(type);
  };

  const closeModal = () => setModal(null);

  const closeAndMutate = () => {
    setModal(null);
    onMutate();
  };

  const actionButtons = (() => {
    if (user.is_scim_synced) {
      return (
        <>
          {user.id && (
            <LineItem icon={SvgEdit} onClick={() => openModal(Modal.EDIT_USER)}>
              Edit User
            </LineItem>
          )}
          <Disabled disabled>
            <LineItem danger icon={SvgUserX}>
              Deactivate User
            </LineItem>
          </Disabled>
          <Separator paddingXRem={0.5} />
          <Text as="p" secondaryBody text03 className="px-3 py-1">
            This is a synced SCIM user managed by your identity provider.
          </Text>
        </>
      );
    }

    switch (user.status) {
      case UserStatus.INVITED:
        return (
          <LineItem
            danger
            icon={SvgXCircle}
            onClick={() => openModal(Modal.CANCEL_INVITE)}
          >
            Cancel Invite
          </LineItem>
        );

      case UserStatus.REQUESTED:
        return (
          <LineItem
            icon={SvgUserCheck}
            onClick={() => {
              setPopoverOpen(false);
              void (async () => {
                try {
                  await approveRequest(user.email);
                  onMutate();
                  toast.success("Request approved");
                } catch (err) {
                  toast.error(err instanceof Error ? err.message : "An error occurred");
                }
              })();
            }}
          >
            Approve
          </LineItem>
        );

      case UserStatus.ACTIVE:
        return (
          <>
            {user.id && (
              <LineItem icon={SvgEdit} onClick={() => openModal(Modal.EDIT_USER)}>
                Edit User
              </LineItem>
            )}
            <LineItem icon={SvgKey} onClick={() => openModal(Modal.RESET_PASSWORD)}>
              Reset Password
            </LineItem>
            <Separator paddingXRem={0.5} />
            <LineItem danger icon={SvgUserX} onClick={() => openModal(Modal.DEACTIVATE)}>
              Deactivate User
            </LineItem>
          </>
        );

      case UserStatus.INACTIVE:
        return (
          <>
            {user.id && (
              <LineItem icon={SvgEdit} onClick={() => openModal(Modal.EDIT_USER)}>
                Edit User
              </LineItem>
            )}
            <LineItem icon={SvgKey} onClick={() => openModal(Modal.RESET_PASSWORD)}>
              Reset Password
            </LineItem>
            <Separator paddingXRem={0.5} />
            <LineItem icon={SvgUserPlus} onClick={() => openModal(Modal.ACTIVATE)}>
              Activate User
            </LineItem>
            <Separator paddingXRem={0.5} />
            <LineItem danger icon={SvgUserX} onClick={() => openModal(Modal.DELETE)}>
              Delete User
            </LineItem>
          </>
        );

      default: {
        const _exhaustive: never = user.status;
        return null;
      }
    }
  })();

  return (
    <>
      <Popover open={popoverOpen} onOpenChange={setPopoverOpen}>
        <Popover.Trigger asChild>
          <Button prominence="tertiary" icon={SvgMoreHorizontal} />
        </Popover.Trigger>
        <Popover.Content align="end" width="sm">
          <Section gap={0.5} height="auto" alignItems="stretch" justifyContent="start">
            {actionButtons}
          </Section>
        </Popover.Content>
      </Popover>

      {modal === Modal.EDIT_USER && user.id && (
        <EditUserModal
          user={user as UserRow & { id: string }}
          onClose={closeModal}
          onMutate={closeAndMutate}
        />
      )}

      {modal === Modal.CANCEL_INVITE && (
        <CancelInviteModal email={user.email} onClose={closeModal} onMutate={onMutate} />
      )}

      {modal === Modal.DEACTIVATE && user.id && (
        <DeactivateUserModal userId={user.id} email={user.email} onClose={closeModal} onMutate={onMutate} />
      )}

      {modal === Modal.ACTIVATE && user.id && (
        <ActivateUserModal userId={user.id} email={user.email} onClose={closeModal} onMutate={onMutate} />
      )}

      {modal === Modal.DELETE && user.id && (
        <DeleteUserModal userId={user.id} email={user.email} onClose={closeModal} onMutate={onMutate} />
      )}

      {modal === Modal.RESET_PASSWORD && (
        <ResetPasswordModal email={user.email} onClose={closeModal} />
      )}
    </>
  );
}
