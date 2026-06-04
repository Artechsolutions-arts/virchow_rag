"use client";

import { useEffect, useState } from "react";
import { LOGOUT_DISABLED } from "@/lib/constants";
import { Notification } from "@/interfaces/settings";
import useSWR, { preload } from "swr";
import { errorHandlingFetcher } from "@/lib/fetcher";
import { checkUserIsNoAuthUser, getUserDisplayName, logout } from "@/lib/user";
import { useUser } from "@/providers/UserProvider";
import InputAvatar from "@/refresh-components/inputs/InputAvatar";
import Text from "@/refresh-components/texts/Text";
import LineItem from "@/refresh-components/buttons/LineItem";
import Popover, { PopoverMenu } from "@/refresh-components/Popover";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { cn } from "@/lib/utils";
import SidebarTab from "@/refresh-components/buttons/SidebarTab";
import NotificationsPopover from "@/sections/sidebar/NotificationsPopover";
import {
  SvgBell,
  SvgExternalLink,
  SvgLogOut,
  SvgUser,
  SvgNotificationBubble,
} from "@opal/icons";
import { Section } from "@/layouts/general-layouts";
import { toast } from "@/hooks/useToast";
import useAppFocus from "@/hooks/useAppFocus";
import { useVectorDbEnabled } from "@/providers/SettingsProvider";

interface SettingsPopoverProps {
  onUserSettingsClick: () => void;
  onOpenNotifications: () => void;
}

function SettingsPopover({
  onUserSettingsClick,
  onOpenNotifications,
}: SettingsPopoverProps) {
  const { user, isAdmin } = useUser();
  const { data: notifications } = useSWR<Notification[]>(
    "/api/notifications",
    errorHandlingFetcher,
    { revalidateOnFocus: false }
  );
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const undismissedCount =
    notifications?.filter((n) => !n.dismissed).length ?? 0;
  const isAnonymousUser =
    user?.is_anonymous_user || checkUserIsNoAuthUser(user?.id ?? "");
  const showLogout = user && !isAnonymousUser && !LOGOUT_DISABLED;
  const showLogin = isAnonymousUser;

  const handleLogin = () => {
    const currentUrl = `${pathname}${searchParams?.toString() ? `?${searchParams.toString()}` : ""
      }`;
    const encodedRedirect = encodeURIComponent(currentUrl);
    router.push(`/auth/login?next=${encodedRedirect}`);
  };

  const handleLogout = () => {
    logout()
      .then((response) => {
        if (!response?.ok) {
          alert("Failed to logout");
          return;
        }

        const currentUrl = `${pathname}${searchParams?.toString() ? `?${searchParams.toString()}` : ""
          }`;

        const encodedRedirect = encodeURIComponent(currentUrl);

        router.push(
          `/auth/login?disableAutoRedirect=true&next=${encodedRedirect}`
        );
      })

      .catch(() => {
        toast.error("Failed to logout");
      });
  };

  return (
    <>
      <PopoverMenu>
        {[
          <div key="user-settings" data-testid="Settings/user-settings">
            <LineItem
              icon={SvgUser}
              href="/app/settings"
              onClick={onUserSettingsClick}
            >
              User Settings
            </LineItem>
          </div>,
          isAdmin && (
            <LineItem
              key="notifications"
              icon={SvgBell}
              onClick={onOpenNotifications}
            >
              {`Notifications${undismissedCount > 0 ? ` (${undismissedCount})` : ""
                }`}
            </LineItem>
          ),
          null,
          showLogin && (
            <LineItem key="log-in" icon={SvgUser} onClick={handleLogin}>
              Log in
            </LineItem>
          ),
          showLogout && (
            <LineItem
              key="log-out"
              icon={SvgLogOut}
              danger
              onClick={handleLogout}
            >
              Log out
            </LineItem>
          ),
        ]}
      </PopoverMenu>
    </>
  );
}

export interface SettingsProps {
  folded?: boolean;
  onShowBuildIntro?: () => void;
}

interface AvatarTriggerProps {
  userDisplayName: string;
  hasNotifications: boolean;
  folded?: boolean;
  popupState: "Settings" | "Notifications" | undefined;
  appFocus: ReturnType<typeof useAppFocus>;
  onToggle?: () => void;
}

function AvatarTrigger({
  userDisplayName,
  hasNotifications,
  folded,
  popupState,
  appFocus,
  onToggle,
}: AvatarTriggerProps) {
  return (
    <div id="virchow-user-dropdown">
      <SidebarTab
        icon={({ className }) => (
          <InputAvatar
            className={cn(
              "flex items-center justify-center bg-background-neutral-inverted-00",
              className,
              "w-5 h-5"
            )}
          >
            <Text as="p" inverted secondaryBody>
              {userDisplayName[0]?.toUpperCase()}
            </Text>
          </InputAvatar>
        )}
        rightChildren={undefined}
        selected={!!popupState || appFocus.isUserSettings()}
        folded={folded}
        onClick={onToggle}
      >
        {userDisplayName}
      </SidebarTab>
    </div>
  );
}

export default function UserAvatarPopover({
  folded,
  onShowBuildIntro,
}: SettingsProps) {
  const [hasMounted, setHasMounted] = useState(false);
  const [popupState, setPopupState] = useState<
    "Settings" | "Notifications" | undefined
  >(undefined);
  const { user } = useUser();
  const router = useRouter();
  const appFocus = useAppFocus();
  const vectorDbEnabled = useVectorDbEnabled();

  // Fetch notifications for display
  // The GET endpoint also triggers a refresh if release notes are stale
  const { data: notifications } = useSWR<Notification[]>(
    "/api/notifications",
    errorHandlingFetcher
  );

  const userDisplayName = getUserDisplayName(user);
  const undismissedCount =
    notifications?.filter((n) => !n.dismissed).length ?? 0;
  const hasNotifications = undismissedCount > 0;

  useEffect(() => {
    setHasMounted(true);
  }, []);

  const handlePopoverOpen = (state: boolean) => {
    if (state) {
      setPopupState("Settings");
    } else {
      setPopupState(undefined);
    }
  };

  const triggerProps: AvatarTriggerProps = {
    userDisplayName,
    hasNotifications,
    folded,
    popupState,
    appFocus,
    onToggle: () => handlePopoverOpen(!popupState),
  };

  if (!hasMounted) {
    return <AvatarTrigger {...triggerProps} />;
  }

  return (
    <Popover open={!!popupState} onOpenChange={handlePopoverOpen}>
      <Popover.Trigger asChild>
        <div>
          <AvatarTrigger {...triggerProps} />
        </div>
      </Popover.Trigger>

      <Popover.Content
        align="end"
        side="right"
        width={popupState === "Notifications" ? "xl" : "md"}
      >
        {popupState === "Settings" && (
          <SettingsPopover
            onUserSettingsClick={() => {
              setPopupState(undefined);
            }}
            onOpenNotifications={() => setPopupState("Notifications")}
          />
        )}
        {popupState === "Notifications" && (
          <NotificationsPopover
            onClose={() => setPopupState("Settings")}
            onNavigate={() => setPopupState(undefined)}
            onShowBuildIntro={onShowBuildIntro}
          />
        )}
      </Popover.Content>
    </Popover>
  );
}
