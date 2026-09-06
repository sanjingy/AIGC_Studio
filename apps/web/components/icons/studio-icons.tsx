import { forwardRef, type ReactNode } from "react";
import type { LucideIcon, LucideProps } from "lucide-react";

/**
 * AIGC Studio 的产品图标。
 *
 * 全套使用同一套 24×24 网格、1.75px 圆角描边与「取景框 / 胶片格 / 时间轴」
 * 母题。组件故意接受 LucideProps，让现有调用点保留 className、size、
 * aria-hidden 与 ref 的写法；图形本身均为仓库内自绘 SVG。
 */
function createStudioIcon(name: string, artwork: ReactNode): LucideIcon {
  const Icon = forwardRef<SVGSVGElement, LucideProps>(
    (
      {
        color = "currentColor",
        size = 24,
        strokeWidth = 1.75,
        absoluteStrokeWidth,
        className,
        ...props
      },
      ref,
    ) => (
      <svg
        ref={ref}
        xmlns="http://www.w3.org/2000/svg"
        width={size}
        height={size}
        viewBox="0 0 24 24"
        fill="none"
        stroke={color}
        strokeWidth={
          absoluteStrokeWidth && typeof size === "number"
            ? (Number(strokeWidth) * 24) / size
            : strokeWidth
        }
        strokeLinecap="round"
        strokeLinejoin="round"
        className={className}
        data-studio-icon=""
        {...props}
      >
        {artwork}
      </svg>
    ),
  );
  Icon.displayName = name;
  return Icon as LucideIcon;
}

export const StudioMarkIcon = createStudioIcon(
  "StudioMarkIcon",
  <>
    <path d="M4.5 7.5h15v11h-15z" />
    <path d="M4.5 7.5 8 4.5h11.5l-3.5 3" />
    <path d="m8.25 4.5 2.75 3m1-3 2.75 3" />
    <path d="M9 13h6m-3-3v6" />
  </>,
);

export const ProjectIcon = createStudioIcon(
  "ProjectIcon",
  <>
    <path d="M4 6.5h6l1.5 2H20v9.5H4z" />
    <path d="M4 10.5h16" />
    <path d="M8 14h3m2 0h3" />
  </>,
);

export const ProjectOverviewIcon = createStudioIcon(
  "ProjectOverviewIcon",
  <>
    <path d="M4 9V5h4M16 5h4v4M20 15v4h-4M8 19H4v-4" />
    <path d="M8.5 9h7v6h-7z" />
    <path d="M12 9v6M8.5 12h7" />
  </>,
);

export const StoryIcon = createStudioIcon(
  "StoryIcon",
  <>
    <path d="M3.5 6.5c2.8-1 5.6-.7 8.5 1v11c-2.9-1.7-5.7-2-8.5-1z" />
    <path d="M20.5 6.5c-2.8-1-5.6-.7-8.5 1v11c2.9-1.7 5.7-2 8.5-1z" />
    <path d="M6.5 10h2.5m-2.5 3h2.5m6-3h2.5m-2.5 3h2.5" />
  </>,
);

export const ScreenplayIcon = createStudioIcon(
  "ScreenplayIcon",
  <>
    <path d="M5 3.5h10l4 4V20.5H5z" />
    <path d="M15 3.5v4h4" />
    <path d="M8 10h3m2 0h3M8 13h8M8 16h5" />
    <path d="M3 7.5h2M3 11.5h2M3 15.5h2" />
  </>,
);

export const CharacterIcon = createStudioIcon(
  "CharacterIcon",
  <>
    <path d="M4 9V5h4M16 5h4v4M20 15v4h-4M8 19H4v-4" />
    <circle cx="12" cy="10" r="2.5" />
    <path d="M7.5 17c.8-2.5 2.3-3.7 4.5-3.7s3.7 1.2 4.5 3.7" />
  </>,
);

export const SceneIcon = createStudioIcon(
  "SceneIcon",
  <>
    <path d="M4 8V5h3M17 5h3v3M20 16v3h-3M7 19H4v-3" />
    <path d="M6.5 16.5 10 12l2.3 2.6 2.2-2.1 3 4" />
    <circle cx="15.5" cy="9" r="1.5" />
    <path d="M6.5 16.5h11" />
  </>,
);

export const StoryboardIcon = createStudioIcon(
  "StoryboardIcon",
  <>
    <rect x="3.5" y="5" width="17" height="14" rx="2" />
    <path d="M9.2 5v14M14.8 5v14M3.5 12h17" />
    <path d="m11.2 9 1.9 1.2-1.9 1.2z" />
  </>,
);

export const QueueIcon = createStudioIcon(
  "QueueIcon",
  <>
    <path d="M5 6.5h11v4H5zM8 13.5h11v4H8z" />
    <path d="M3.5 8.5H2.5m3 7H4.5" />
    <path d="M18.5 6.5h1.5v4h-1.5" />
    <circle cx="6" cy="15.5" r=".5" />
  </>,
);

export const AssetLibraryIcon = createStudioIcon(
  "AssetLibraryIcon",
  <>
    <path d="M4 6h16v13H4z" />
    <path d="M7 6V4h4l1.5 2M4 9h16" />
    <path d="M7 12h4v4H7zM14 12h3v4h-3" />
  </>,
);

export const SettingsIcon = createStudioIcon(
  "SettingsIcon",
  <>
    <path d="M4 7h16M4 12h16M4 17h16" />
    <path d="M8 5v4M15 10v4M11 15v4" />
  </>,
);

export const RenderImageIcon = createStudioIcon(
  "RenderImageIcon",
  <>
    <path d="M4 8V5h3M17 5h3v3M20 16v3h-3M7 19H4v-3" />
    <path d="m6.5 16 3.2-3.5 2.4 2.5 2-2 3.4 3" />
    <path d="M15 8h3m-1.5-1.5v3" />
  </>,
);

export const VideoIcon = createStudioIcon(
  "VideoIcon",
  <>
    <rect x="3.5" y="5" width="17" height="14" rx="2" />
    <path d="M7 5v3m5-3v3m5-3v3M7 16v3m5-3v3m5-3v3" />
    <path d="m10 9.5 4 2.5-4 2.5z" />
  </>,
);

export const VoiceIcon = createStudioIcon(
  "VoiceIcon",
  <>
    <path d="M4 7v10M20 7v10" />
    <path d="M7 12h1.5l1-4 2 8 1.8-7 1.4 6 1-3H17" />
    <path d="M4 5h16M4 19h16" />
  </>,
);

export const GateApprovedIcon = createStudioIcon(
  "GateApprovedIcon",
  <>
    <path d="M5 19V6.5h3M19 19V6.5h-3M8 9h8M8 14h3" />
    <path d="m12.5 15 1.7 1.7 3.8-4" />
  </>,
);

export const GatePendingIcon = createStudioIcon(
  "GatePendingIcon",
  <>
    <path d="M5 19V6.5h3M19 19V6.5h-3M8 9h8" />
    <circle cx="13" cy="14.5" r="3.5" />
    <path d="M13 12.5v2.2l1.4.8" />
  </>,
);

export const MissingFrameIcon = createStudioIcon(
  "MissingFrameIcon",
  <>
    <path d="M5 9V5h4M15 5h4v4M19 15v4h-4M9 19H5v-4" />
    <path d="M9 12h6" />
  </>,
);

export const StaleIcon = createStudioIcon(
  "StaleIcon",
  <>
    <path d="M5 8V5h3M16 5h3v3M19 16v3h-3M8 19H5v-3" />
    <path d="M8.2 11a4.5 4.5 0 0 1 7.6-1.6L17 11" />
    <path d="M17 8v3h-3" />
    <path d="M15.8 13a4.5 4.5 0 0 1-7.6 1.6L7 13" />
    <path d="M7 16v-3h3" />
  </>,
);

export const ModelRackIcon = createStudioIcon(
  "ModelRackIcon",
  <>
    <path d="M4 5.5h16v4H4zM4 14.5h16v4H4z" />
    <circle cx="7" cy="7.5" r=".7" />
    <circle cx="7" cy="16.5" r=".7" />
    <path d="M11 7.5h6M11 16.5h6" />
  </>,
);
