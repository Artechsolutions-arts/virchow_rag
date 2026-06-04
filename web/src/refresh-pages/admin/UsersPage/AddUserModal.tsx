"use client";

import { useState, useCallback } from "react";
import { Button } from "@opal/components";
import { SvgUsers } from "@opal/icons";
import { Disabled } from "@opal/core";
import Modal, { BasicModalFooter } from "@/refresh-components/Modal";
import InputTypeIn from "@/refresh-components/inputs/InputTypeIn";
import PasswordInputTypeIn from "@/refresh-components/inputs/PasswordInputTypeIn";
import InputSelect from "@/refresh-components/inputs/InputSelect";
import Text from "@/refresh-components/texts/Text";
import { toast } from "@/hooks/useToast";
import { addUser } from "./svc";

interface AddUserModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onMutate?: () => void;
}

export default function AddUserModal({
  open,
  onOpenChange,
  onMutate,
}: AddUserModalProps) {
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [department, setDepartment] = useState<string>("QA");
  const [company, setCompany] = useState<string>("Virchow");
  const [status, setStatus] = useState<string>("active");
  const [role, setRole] = useState<string>("user");
  const [mobileNumber, setMobileNumber] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleClose = useCallback(() => {
    onOpenChange(false);
    setTimeout(() => {
      setEmail("");
      setUsername("");
      setPassword("");
      setDepartment("QA");
      setCompany("Virchow");
      setStatus("active");
      setRole("user");
      setMobileNumber("");
      setIsSubmitting(false);
    }, 200);
  }, [onOpenChange]);

  const handleOpenChange = useCallback(
    (next: boolean) => {
      if (!next) {
        if (!isSubmitting) handleClose();
      } else {
        onOpenChange(next);
      }
    },
    [handleClose, isSubmitting, onOpenChange]
  );

  async function handleAddUser() {
    if (!email || !username || !password || !department || !company || !status || !role || !mobileNumber) {
      toast.error("Please fill in all fields.");
      return;
    }

    setIsSubmitting(true);
    try {
      await addUser({
        email,
        personal_name: username,
        password,
        department,
        company,
        status,
        role,
        mobile_number: mobileNumber,
      });
      toast.success("User added successfully.");
      onMutate?.();
      handleClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to add user.");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <Modal open={open} onOpenChange={handleOpenChange}>
      <Modal.Content width="sm" height="fit">
        <Modal.Header
          icon={SvgUsers}
          title="Add User"
          onClose={isSubmitting ? undefined : handleClose}
        />

        <Modal.Body>
          <div className="flex flex-col gap-4">
            <div className="flex flex-row gap-4">
            <div className="flex flex-col gap-1">
              <Text secondaryBody text03>Email</Text>
              <InputTypeIn
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="user@example.com"
                spellCheck={false}
              />
            </div>

            <div className="flex flex-col gap-1">
              <Text secondaryBody text03>Username</Text>
              <InputTypeIn
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="johndoe"
                spellCheck={false}
              />
            </div>
            </div>
            
            <div className="flex flex-row gap-4">
            <div className="flex flex-col gap-1" style={{width: "50%"}}>
              <Text secondaryBody text03>Password</Text>
              <PasswordInputTypeIn
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Secure password"
              />
            </div>

            <div className="flex flex-col gap-1" style={{width: "50%"}}>
              <Text secondaryBody text03>Company</Text>
              <InputSelect value={company} onValueChange={setCompany}>
                <InputSelect.Trigger />
                <InputSelect.Content>
                  <InputSelect.Item value="Virchow">Virchow</InputSelect.Item>
                  <InputSelect.Item value="Emnar">Emnar</InputSelect.Item>
                </InputSelect.Content>
              </InputSelect>
            </div>
            </div>
            
            <div className="flex flex-row gap-4">
            <div className="flex flex-col gap-1" style={{width: "75%"}}>
              <Text secondaryBody text03>Department</Text>
              <InputSelect value={department} onValueChange={setDepartment}>
                <InputSelect.Trigger />
                <InputSelect.Content>
                  <InputSelect.Item value="QA">QA</InputSelect.Item>
                  <InputSelect.Item value="Sales">Sales</InputSelect.Item>
                  <InputSelect.Item value="Accounts">Accounts</InputSelect.Item>
                  <InputSelect.Item value="Production">Production</InputSelect.Item>
                </InputSelect.Content>
              </InputSelect>
            </div>

            <div className="flex flex-col gap-1" style={{width: "75%"}}>
              <Text secondaryBody text03>Status</Text>
              <InputSelect value={status} onValueChange={setStatus}>
                <InputSelect.Trigger />
                <InputSelect.Content>
                  <InputSelect.Item value="active">Active</InputSelect.Item>
                  <InputSelect.Item value="hold">Hold</InputSelect.Item>
                  <InputSelect.Item value="terminated">Terminated</InputSelect.Item>
                </InputSelect.Content>
              </InputSelect>
            </div>
            </div>

            <div className="flex flex-row gap-4">
            <div className="flex flex-col gap-1" style={{width: "50%"}}>
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

            <div className="flex flex-col gap-1" style={{width: "50%"}}>
              <Text secondaryBody text03>Mobile Number</Text>
              <InputTypeIn
                value={mobileNumber}
                onChange={(e) => setMobileNumber(e.target.value)}
                placeholder="+1234567890"
              />
            </div>
            </div>
          </div>
        </Modal.Body>

        <Modal.Footer>
          <BasicModalFooter
            cancel={
              <Disabled disabled={isSubmitting}>
                <Button prominence="tertiary" onClick={handleClose}>
                  Cancel
                </Button>
              </Disabled>
            }
            submit={
              <Disabled disabled={isSubmitting}>
                <Button onClick={handleAddUser}>Add User</Button>
              </Disabled>
            }
          />
        </Modal.Footer>
      </Modal.Content>
    </Modal>
  );
}
