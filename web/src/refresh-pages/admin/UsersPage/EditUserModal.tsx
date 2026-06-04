"use client";

import { useState, useCallback } from "react";
import { Button } from "@opal/components";
import { SvgUser } from "@opal/icons";
import { Disabled } from "@opal/core";
import Modal, { BasicModalFooter } from "@/refresh-components/Modal";
import InputTypeIn from "@/refresh-components/inputs/InputTypeIn";
import InputSelect from "@/refresh-components/inputs/InputSelect";
import Text from "@/refresh-components/texts/Text";
import { toast } from "@/hooks/useToast";
import type { UserRow } from "./interfaces";

interface EditUserModalProps {
  user: UserRow & { id: string };
  onClose: () => void;
  onMutate: () => void;
}

export default function EditUserModal({
  user,
  onClose,
  onMutate,
}: EditUserModalProps) {
  const [name, setName] = useState(user.personal_name ?? "");
  const [department, setDepartment] = useState(user.department ?? "");
  const [role, setRole] = useState(user.role ?? "user");
  const [status, setStatus] = useState(user.is_active ? "active" : "inactive");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSave = useCallback(async () => {
    if (!name.trim()) {
      toast.error("Name cannot be empty.");
      return;
    }
    setIsSubmitting(true);
    try {
      const res = await fetch(`/api/manage/admin/users/${user.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          personal_name: name.trim(),
          department: department.trim(),
          role,
          status,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail ?? "Failed to update user");
      }
      toast.success("User updated successfully.");
      onMutate();
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to update user.");
    } finally {
      setIsSubmitting(false);
    }
  }, [name, department, role, status, user.id, onMutate, onClose]);

  return (
    <Modal open onOpenChange={(open) => !open && !isSubmitting && onClose()}>
      <Modal.Content width="sm" height="fit">
        <Modal.Header
          icon={SvgUser}
          title="Edit User"
          description={user.personal_name ? `${user.personal_name} (${user.email})` : user.email}
          onClose={isSubmitting ? undefined : onClose}
        />

        <Modal.Body>
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-1">
              <Text secondaryBody text03>Display Name</Text>
              <InputTypeIn
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Full name"
                spellCheck={false}
              />
            </div>

            <div className="flex flex-col gap-1">
              <Text secondaryBody text03>Department</Text>
              <InputSelect value={department} onValueChange={setDepartment}>
                <InputSelect.Trigger />
                <InputSelect.Content>
                  <InputSelect.Item value="QA">QA</InputSelect.Item>
                  <InputSelect.Item value="Sales">Sales</InputSelect.Item>
                  <InputSelect.Item value="Accounts">Accounts</InputSelect.Item>
                  <InputSelect.Item value="Production">Production</InputSelect.Item>
                  <InputSelect.Item value="Marketing">Marketing</InputSelect.Item>
                  <InputSelect.Item value="Administration">Administration</InputSelect.Item>
                  <InputSelect.Item value="Plant">Plant</InputSelect.Item>
                  <InputSelect.Item value="Default">Default</InputSelect.Item>
                </InputSelect.Content>
              </InputSelect>
            </div>

            <div className="flex flex-row gap-4">
              <div className="flex flex-col gap-1" style={{ width: "50%" }}>
                <Text secondaryBody text03>Role</Text>
                <InputSelect value={role} onValueChange={setRole}>
                  <InputSelect.Trigger />
                  <InputSelect.Content>
                    <InputSelect.Item value="user">User</InputSelect.Item>
                    <InputSelect.Item value="admin">Admin</InputSelect.Item>
                    <InputSelect.Item value="hod">HOD</InputSelect.Item>
                  </InputSelect.Content>
                </InputSelect>
              </div>

              <div className="flex flex-col gap-1" style={{ width: "50%" }}>
                <Text secondaryBody text03>Status</Text>
                <InputSelect value={status} onValueChange={setStatus}>
                  <InputSelect.Trigger />
                  <InputSelect.Content>
                    <InputSelect.Item value="active">Active</InputSelect.Item>
                    <InputSelect.Item value="inactive">Inactive</InputSelect.Item>
                  </InputSelect.Content>
                </InputSelect>
              </div>
            </div>
          </div>
        </Modal.Body>

        <Modal.Footer>
          <BasicModalFooter
            cancel={
              <Disabled disabled={isSubmitting}>
                <Button prominence="tertiary" onClick={onClose}>Cancel</Button>
              </Disabled>
            }
            submit={
              <Disabled disabled={isSubmitting}>
                <Button onClick={handleSave}>Save Changes</Button>
              </Disabled>
            }
          />
        </Modal.Footer>
      </Modal.Content>
    </Modal>
  );
}
