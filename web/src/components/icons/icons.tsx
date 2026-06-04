"use client";

import { JSX } from "react";
import Image from "next/image";
import { StaticImageData } from "next/image";
import { cn } from "@/lib/utils";
import { BrainIcon as Brain } from "@phosphor-icons/react";
import {
    FiAlertCircle,
    FiAlertTriangle,
    FiChevronDown,
    FiChevronsDown,
    FiChevronsUp,
    FiClipboard,
    FiCpu,
    FiDatabase,
    FiEdit2,
    FiFile,
    FiGlobe,
    FiInfo,
    FiMail,
} from "react-icons/fi";
import { FaRobot } from "react-icons/fa";
import { SiBookstack } from "react-icons/si";

export interface IconProps {
    size?: number;
    className?: string;
}
export interface LogoIconProps extends IconProps {
    src: string | StaticImageData;
}
export type VirchowIconType = (props: IconProps) => JSX.Element;

export const defaultTailwindCSS = "my-auto flex flex-shrink-0 text-default";
export const defaultTailwindCSSBlue = "my-auto flex flex-shrink-0 text-link";

export const LogoIcon = ({
    size = 16,
    className = defaultTailwindCSS,
    src,
}: LogoIconProps) => (
    <Image
        style={{ width: `${size}px`, height: `${size}px` }}
        className={`w-[${size}px] h-[${size}px] object-contain ` + className}
        src={src}
        alt="Logo"
        width="96"
        height="96"
    />
);

// Helper to create simple icon components from react-icon libraries
export function createIcon(
    IconComponent: React.ComponentType<{ size?: number; className?: string }>
) {
    function IconWrapper({
        size = 16,
        className = defaultTailwindCSS,
    }: IconProps) {
        return <IconComponent size={size} className={className} />;
    }

    IconWrapper.displayName = `Icon(${IconComponent.displayName || IconComponent.name || "Component"
        })`;
    return IconWrapper;
}

/**
 * Creates a logo icon component that automatically supports dark mode adaptations.
 *
 * Depending on the options provided, the returned component handles:
 * 1. Light/Dark variants: If both `src` and `darkSrc` are provided, displays the
 *    appropriate image based on the current color theme.
 * 2. Monochromatic inversion: If `monochromatic` is true, applies a CSS color inversion
 *    in dark mode for a monochrome icon appearance.
 * 3. Static icon: If only `src` is provided, renders the image without dark mode adaptation.
 *
 * @param src - The image or SVG source used for the icon (light/default mode).
 * @param options - Optional settings:
 *   - darkSrc: The image or SVG source used specifically for dark mode.
 *   - monochromatic: If true, applies a CSS inversion in dark mode for monochrome logos.
 *   - sizeAdjustment: Number to add to the icon size (e.g., 4 to make icon larger).
 *   - classNameAddition: Additional CSS classes to apply (e.g., '-m-0.5' for margin).
 * @returns A React functional component that accepts {@link IconProps} and renders
 *          the logo with dark mode handling as needed.
 */
const createLogoIcon = (
    src: string | StaticImageData,
    options?: {
        darkSrc?: string | StaticImageData;
        monochromatic?: boolean;
        sizeAdjustment?: number;
        classNameAddition?: string;
    }
) => {
    const {
        darkSrc,
        monochromatic,
        sizeAdjustment = 0,
        classNameAddition = "",
    } = options || {};

    const LogoIconWrapper = ({
        size = 16,
        className = defaultTailwindCSS,
    }: IconProps) => {
        const adjustedSize = size + sizeAdjustment;

        // Build className dynamically (only apply monochromatic if no darkSrc)
        const monochromaticClass = !darkSrc && monochromatic ? "dark:invert" : "";
        const finalClassName = [className, classNameAddition, monochromaticClass]
            .filter(Boolean)
            .join(" ");

        // If darkSrc is provided, use CSS-based dark mode switching
        // This avoids hydration issues and content flashing since next-themes
        // sets the .dark class before React hydrates
        if (darkSrc) {
            return (
                <>
                    <LogoIcon
                        size={adjustedSize}
                        className={`${finalClassName} dark:hidden`}
                        src={src}
                    />
                    <LogoIcon
                        size={adjustedSize}
                        className={`${finalClassName} hidden dark:block`}
                        src={darkSrc}
                    />
                </>
            );
        }

        return (
            <LogoIcon size={adjustedSize} className={finalClassName} src={src} />
        );
    };

    LogoIconWrapper.displayName = "LogoIconWrapper";
    return LogoIconWrapper;
};

// ============================================================================
// GENERIC SVG COMPONENTS (sorted alphabetically)
// ============================================================================
export const AlertIcon = createIcon(FiAlertCircle);
export const ArtAsistantIcon = ({
    size = 24,
    className = defaultTailwindCSS,
}: IconProps) => {
    return (
        <svg
        />
    );
};
export const BookmarkIcon = ({
    size = 16,
    className = defaultTailwindCSS,
}: IconProps) => {
    return (
        <svg
            style={{ width: `${size}px`, height: `${size}px` }}
            className={`w-[${size}px] h-[${size}px] ` + className}
            xmlns="http://www.w3.org/2000/svg"
            width="200"
            height="200"
            viewBox="0 0 16 16"
        >
            <path
                fill="currentColor"
                d="M3.75 2a.75.75 0 0 0-.75.75v10.5a.75.75 0 0 0 1.28.53L8 10.06l3.72 3.72a.75.75 0 0 0 1.28-.53V2.75a.75.75 0 0 0-.75-.75z"
            />
        </svg>
    );
};
export const BrainIcon = ({
    size = 16,
    className = defaultTailwindCSS,
}: IconProps) => {
    return <Brain size={size} className={className} />;
};
export const CPUIcon = createIcon(FiCpu);
export const DatabaseIcon = createIcon(FiDatabase);
export const CameraIcon = ({
    size = 16,
    className = defaultTailwindCSS,
}: IconProps) => {
    return (
        <svg
            style={{ width: `${size}px`, height: `${size}px` }}
            className={`w-[${size}px] h-[${size}px] ` + className}
            xmlns="http://www.w3.org/2000/svg"
            width="200"
            height="200"
            viewBox="0 0 14 14"
        >
            <g
                fill="none"
                stroke="currentColor"
                strokeLinecap="round"
                strokeLinejoin="round"
            >
                <path d="M13.5 5a1 1 0 0 0-1-1h-2L9 2H5L3.5 4h-2a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h11a1 1 0 0 0 1-1z" />
                <path d="M7 9.75a2.25 2.25 0 1 0 0-4.5a2.25 2.25 0 0 0 0 4.5" />
            </g>
        </svg>
    );
};
export const Caret = ({
    size = 24,
    className = defaultTailwindCSS,
}: IconProps) => {
    return (
        <svg
            style={{ width: `${size}px`, height: `${size}px` }}
            className={`w-[${size}px] h-[${size}px] ` + className}
            xmlns="http://www.w3.org/2000/svg"
            width="200"
            height="200"
            viewBox="0 0 24 24"
        >
            <path
                fill="currentColor"
                d="m12.37 15.835l6.43-6.63C19.201 8.79 18.958 8 18.43 8H5.57c-.528 0-.771.79-.37 1.205l6.43 6.63c.213.22.527.22.74 0Z"
            />
        </svg>
    );
};
export const CheckmarkIcon = ({
    size = 16,
    className = defaultTailwindCSS,
}: IconProps) => {
    return (
        <svg
            style={{ width: `${size}px`, height: `${size}px` }}
            className={`w-[${size}px] h-[${size}px] ` + className}
            xmlns="http://www.w3.org/2000/svg"
            width="200"
            height="200"
            viewBox="0 0 24 24"
        >
            <path
                fill="none"
                stroke="currentColor"
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth="2"
                d="M20 6L9 17l-5-5"
            />
        </svg>
    );
};
export const ChevronDownIcon = createIcon(FiChevronDown);
export const ChevronsDownIcon = createIcon(FiChevronsDown);
export const ChevronsUpIcon = createIcon(FiChevronsUp);
export const ClipboardIcon = createIcon(FiClipboard);
export const DexpandTwoIcon = ({
    size = 16,
    className = defaultTailwindCSS,
}: IconProps) => {
    return (
        <svg
            style={{ width: `${size}px`, height: `${size}px` }}
            className={`w-[${size}px] h-[${size}px] ` + className}
            xmlns="http://www.w3.org/2000/svg"
            width="200"
            height="200"
            viewBox="0 0 14 14"
        >
            <path
                fill="none"
                stroke="currentColor"
                strokeLinecap="round"
                strokeLinejoin="round"
                d="m.5 13.5l5-5m-4 0h4v4m8-12l-5 5m4 0h-4v-4"
            />
        </svg>
    );
};
export const DocumentIcon2 = ({
    size = 16,
    className = defaultTailwindCSS,
}: IconProps) => {
    return (
        <svg
            style={{ width: `${size}px`, height: `${size}px` }}
            className={`w-[${size}px] h-[${size}px] ` + className}
            xmlns="http://www.w3.org/2000/svg"
            width="200"
            height="200"
            viewBox="0 0 24 24"
        >
            <path
                fill="none"
                stroke="currentColor"
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth="1.5"
                d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z"
            />
        </svg>
    );
};
export const DownloadCSVIcon = ({
    size = 16,
    className = defaultTailwindCSS,
}: IconProps) => {
    return (
        <svg
            style={{ width: `${size}px`, height: `${size}px` }}
            className={`w-[${size}px] h-[${size}px] ` + className}
            xmlns="http://www.w3.org/2000/svg"
            width="200"
            height="200"
            viewBox="0 0 14 14"
        >
            <path
                fill="none"
                stroke="currentColor"
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M.5 10.5v1a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2v-1M4 6l3 3.5L10 6M7 9.5v-9"
            />
        </svg>
    );
};
export const EditIcon = createIcon(FiEdit2);
export const EmailIcon = createIcon(FiMail);

//  COMPANY LOGOS
export const AirtableIcon = createIcon(FiFile);
export const AsanaIcon = createIcon(FiFile);
export const AxeroIcon = createIcon(FiFile);
export const BitbucketIcon = createIcon(FiFile);
export const BookstackIcon = createIcon(SiBookstack);
export const ClickupIcon = createIcon(FiFile);
export const CodaIcon = createIcon(FiFile);
export const ColorDiscordIcon = createIcon(FiFile);
export const ColorSlackIcon = createIcon(FiFile);
export const ConfluenceIcon = createIcon(FiFile);
export const DiscourseIcon = createIcon(FiFile);
export const Document360Icon = createIcon(FiFile);
export const DropboxIcon = createIcon(FiFile);
export const DrupalWikiIcon = createIcon(FiFile);
export const EgnyteIcon = createIcon(FiFile);
export const FirefliesIcon = createIcon(FiFile);
export const FreshdeskIcon = createIcon(FiFile);
export const GitbookIcon = createIcon(FiFile);
export const GithubIcon = createIcon(FiFile);
export const GitlabIcon = createIcon(FiFile);
export const GmailIcon = createIcon(FiFile);
export const GongIcon = createIcon(FiFile);
export const GoogleDriveIcon = createIcon(FiFile);
export const GoogleSitesIcon = createIcon(FiFile);
export const GoogleStorageIcon = createIcon(FiFile);
export const GuruIcon = createIcon(FiFile);
export const HighspotIcon = createIcon(FiFile);
export const HubSpotIcon = createIcon(FiFile);
export const JiraIcon = createIcon(FiFile);
export const LinearIcon = createIcon(FiFile);
export const LoopioIcon = createIcon(FiFile);
export const MediaWikiIcon = createIcon(FiFile);
export const NotionIcon = createIcon(FiFile);
export const OCIStorageIcon = createIcon(FiFile);
export const OutlineIcon = createIcon(FiFile);
export const ProductboardIcon = createIcon(FiFile);
export const R2Icon = createIcon(FiFile);
export const S3Icon = createIcon(FiFile);
export const SalesforceIcon = createIcon(FiFile);
export const SharepointIcon = createIcon(FiFile);
export const SlabIcon = createIcon(FiFile);
export const TeamsIcon = createIcon(FiFile);
export const TestRailIcon = createIcon(FiFile);
export const WikipediaIcon = createIcon(FiFile);
export const XenforoIcon = createIcon(FiFile);
export const ZendeskIcon = createIcon(FiFile);
export const ZulipIcon = createIcon(FiFile);
export const ExpandTwoIcon = ({
    size = 16,
    className = defaultTailwindCSS,
}: IconProps) => {
    return (
        <svg
            style={{ width: `${size}px`, height: `${size}px` }}
            className={`w-[${size}px] h-[${size}px] ` + className}
            xmlns="http://www.w3.org/2000/svg"
            width="200"
            height="200"
            viewBox="0 0 14 14"
        >
            <path
                fill="none"
                stroke="currentColor"
                strokeLinecap="round"
                strokeLinejoin="round"
                d="m8.5 5.5l5-5m-4 0h4v4m-8 4l-5 5m4 0h-4v-4"
            />
        </svg>
    );
};
export const FileIcon = createIcon(FiFile);
export const FileOptionIcon = ({
    size = 24,
    className = defaultTailwindCSS,
}: IconProps) => {
    return (
        <svg
            style={{ width: `${size}px`, height: `${size}px` }}
            className={`w-[${size}px] h-[${size}px] ` + className}
            width="24"
            height="24"
            viewBox="0 0 24 24"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
        >
            <path
                d="M20.6801 7.02928C20.458 6.5654 20.1451 6.15072 19.76 5.80973L16.76 3.09074C16.0939 2.47491 15.2435 2.09552 14.3401 2.01115C14.2776 1.99628 14.2125 1.99628 14.15 2.01115H8.21008C7.54764 1.98307 6.88617 2.08698 6.26428 2.31683C5.64239 2.54667 5.07249 2.89785 4.58765 3.34995C4.10281 3.80205 3.71274 4.34605 3.44019 4.95025C3.16763 5.55445 3.01797 6.20679 3 6.86934V17.1655C3.03538 18.1647 3.36978 19.1303 3.95984 19.9375C4.5499 20.7448 5.36855 21.3566 6.31006 21.6939C6.92247 21.9253 7.57613 22.0274 8.22998 21.9937H15.79C16.4525 22.0218 17.1138 21.9179 17.7357 21.6881C18.3576 21.4582 18.9276 21.107 19.4125 20.6549C19.8973 20.2028 20.2874 19.6588 20.5599 19.0546C20.8325 18.4504 20.982 17.7981 21 17.1355V8.56872C21.0034 8.03873 20.8944 7.51404 20.6801 7.02928ZM16.0601 7.41915C15.9174 7.42047 15.7759 7.39353 15.6437 7.33986C15.5115 7.2862 15.3913 7.20687 15.2899 7.10649C15.1886 7.00611 15.1081 6.88664 15.0532 6.755C14.9983 6.62336 14.97 6.48215 14.97 6.33953V3.69052C15.63 3.85046 18.2 6.48947 18.76 6.92931C18.9256 7.06878 19.0675 7.23423 19.1801 7.41915H16.0601Z"
                fill="currentColor"
            />
        </svg>
    );
};
export const GlobeIcon = createIcon(FiGlobe);
export const GroupsIconSkeleton = ({
    size = 16,
    className = defaultTailwindCSS,
}: IconProps) => {
    return (
        <svg
            style={{ width: `${size}px`, height: `${size}px` }}
            className={`w-[${size}px] h-[${size}px] ` + className}
            xmlns="http://www.w3.org/2000/svg"
            width="200"
            height="200"
            viewBox="0 0 24 24"
        >
            <g fill="none" stroke="currentColor" strokeWidth="1.5">
                <circle cx="9" cy="6" r="4" />
                <path strokeLinecap="round" d="M15 9a3 3 0 1 0 0-6" />
                <ellipse cx="9" cy="17" rx="7" ry="4" />
                <path
                    strokeLinecap="round"
                    d="M18 14c1.754.385 3 1.359 3 2.5c0 1.03-1.014 1.923-2.5 2.37"
                />
            </g>
        </svg>
    );
};
export const InfoIcon = createIcon(FiInfo);
export const MacIcon = ({
    size = 16,
    className = "my-auto flex flex-shrink-0 ",
}: IconProps) => {
    return (
        <svg
            style={{ width: `${size}px`, height: `${size}px` }}
            className={`w-[${size}px] h-[${size}px] ` + className}
            xmlns="http://www.w3.org/2000/svg"
            width="200"
            height="200"
            viewBox="0 0 24 24"
        >
            <path
                fill="currentColor"
                d="M6.5 4.5a2 2 0 0 1 2 2v2h-2a2 2 0 1 1 0-4Zm4 4v-2a4 4 0 1 0-4 4h2v3h-2a4 4 0 1 0 4 4v-2h3v2a4 4 0 1 0 4-4h-2v-3h2a4 4 0 1 0-4-4v2h-3Zm0 2h3v3h-3v-3Zm5-2v-2a2 2 0 1 1 2 2h-2Zm0 7h2a2 2 0 1 1-2 2v-2Zm-7 0v2a2 2 0 1 1-2-2h2Z"
            />
        </svg>
    );
};
export const NewChatIcon = ({
    size = 24,
    className = defaultTailwindCSS,
}: IconProps) => {
    return (
        <svg
            style={{ width: `${size}px`, height: `${size}px` }}
            className={`w-[${size}px] h-[${size}px] ` + className}
            viewBox="0 0 20 20"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
        >
            <path
                d="M12.5 1.99982H6C3.79086 1.99982 2 3.79068 2 5.99982V13.9998C2 16.209 3.79086 17.9998 6 17.9998H14C16.2091 17.9998 18 16.209 18 13.9998V8.49982"
                stroke="currentColor"
                strokeLinecap="round"
            />
            <path
                d="M17.1471 5.13076C17.4492 4.82871 17.6189 4.41901 17.619 3.9918C17.6191 3.56458 17.4494 3.15484 17.1474 2.85271C16.8453 2.55058 16.4356 2.38082 16.0084 2.38077C15.5812 2.38071 15.1715 2.55037 14.8693 2.85242L11.0562 6.66651L7.24297 10.4806C7.1103 10.6129 7.01218 10.7758 6.95726 10.9549L6.20239 13.4418C6.18762 13.4912 6.18651 13.5437 6.19916 13.5937C6.21182 13.6437 6.23778 13.6894 6.27428 13.7258C6.31078 13.7623 6.35646 13.7881 6.40648 13.8007C6.45651 13.8133 6.509 13.8121 6.5584 13.7972L9.04585 13.0429C9.2248 12.9885 9.38766 12.891 9.52014 12.7589L17.1471 5.13076Z"
                stroke="currentColor"
                strokeLinecap="round"
                strokeLinejoin="round"
            />
        </svg>
    );
};
export const NotebookIcon = ({
    size = 16,
    className = defaultTailwindCSS,
}: IconProps) => {
    return (
        <svg
            style={{ width: `${size}px`, height: `${size}px` }}
            className={`w-[${size}px] h-[${size}px] ` + className}
            xmlns="http://www.w3.org/2000/svg"
            width="200"
            height="200"
            viewBox="0 0 24 24"
        >
            <path
                fill="currentColor"
                d="M11.25 4.533A9.707 9.707 0 0 0 6 3a9.735 9.735 0 0 0-3.25.555a.75.75 0 0 0-.5.707v14.25a.75.75 0 0 0 1 .707A8.237 8.237 0 0 1 6 18.75c1.995 0 3.823.707 5.25 1.886V4.533Zm1.5 16.103A8.214 8.214 0 0 1 18 18.75c.966 0 1.89.166 2.75.47a.75.75 0 0 0 1-.708V4.262a.75.75 0 0 0-.5-.707A9.735 9.735 0 0 0 18 3a9.707 9.707 0 0 0-5.25 1.533v16.103Z"
            />
        </svg>
    );
};
export const NotebookIconSkeleton = ({
    size = 16,
    className = defaultTailwindCSS,
}: IconProps) => {
    return (
        <svg
            style={{ width: `${size}px`, height: `${size}px` }}
            className={`w-[${size}px] h-[${size}px] ` + className}
            xmlns="http://www.w3.org/2000/svg"
            width="200"
            height="200"
            viewBox="0 0 24 24"
        >
            <path
                fill="none"
                stroke="currentColor"
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth="1.5"
                d="M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25"
            />
        </svg>
    );
};
export const VirchowSidebarIcon = ({
    size = 16,
    className = defaultTailwindCSS,
}: IconProps) => {
    return (
        <Image
            src="/virchow_sidebar_logo.svg"
            alt="Virchow Sidebar Logo"
            width={size}
            height={size}
            className={cn("object-contain", className)}
            style={{ width: "auto", height: "auto" }}
        />
    );
};

export const VirchowMainIcon = ({
    size = 16,
    className = defaultTailwindCSS,
}: IconProps) => {
    return (
        <Image
            src="/virchow_sidebar_logo.svg"
            alt="Virchow Logo"
            width={size}
            height={size}
            className={cn("object-contain", className)}
        />
    );
};

export const VirchowIcon = VirchowSidebarIcon;
export const VirchowLogoTypeIcon = VirchowMainIcon;



// AUTO-ADDED MISSING EXPORTS
export const AmazonIcon = createIcon(FiFile);
export const AnthropicIcon = createIcon(FiFile);
export const AzureIcon = createIcon(FiFile);
export const BoxIcon = createIcon(FiFile);
export const CohereIcon = createIcon(FiFile);
export const DeepseekIcon = createIcon(FiFile);
export const ElevenLabsIcon = createIcon(FiFile);
export const GeminiIcon = createIcon(FiFile);
export const GoogleIcon = createIcon(FiFile);
export const LMStudioIcon = createIcon(FiFile);
export const LiteLLMIcon = createIcon(FiFile);
export const MetaIcon = createIcon(FiFile);
export const MicrosoftIcon = createIcon(FiFile);
export const MicrosoftIconSVG = createIcon(FiFile);
export const MistralIcon = createIcon(FiFile);
export const MixedBreadIcon = createIcon(FiFile);
export const NomicIcon = createIcon(FiFile);
export const OllamaIcon = createIcon(FiFile);
export const OneDriveIcon = createIcon(FiFile);
export const OpenAIISVG = createIcon(FiFile);
export const OpenAIIcon = createIcon(FiFile);
export const OpenAISVG = createIcon(FiFile);
export const OpenSourceIcon = createIcon(FiFile);
export const OutlookIcon = createIcon(FiFile);
export const QwenIcon = createIcon(FiFile);
export const RobotIcon = createIcon(FiFile);
export const ServiceNowIcon = createIcon(FiFile);
export const SwapIcon = createIcon(FiFile);
export const TrelloIcon = createIcon(FiFile);
export const TriangleAlertIcon = createIcon(FiFile);
export const VoyageIconSVG = createIcon(FiFile);
export const WindowsIcon = createIcon(FiFile);
export const ZAIIcon = createIcon(FiFile);
