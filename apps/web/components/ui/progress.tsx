import { cn } from "@/lib/utils";

/**
 * 进度条。值在渲染前钳到 0–100：进度来自后端任务，越界的数字
 * （负数、超过 100）会让轨道被填出容器外，比显示错数字更难发现。
 *
 * `label` 是必填的：`role="progressbar"` 没有可见文字时，读屏只会念
 * 一个百分比，用户不知道是哪一条在跑。
 */
export function Progress({
  value,
  label,
  className,
}: {
  value: number;
  label: string;
  className?: string;
}) {
  const clamped = Math.round(Math.min(100, Math.max(0, value)));

  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={clamped}
      className={cn("h-1 overflow-hidden rounded-[1px] bg-border", className)}
    >
      <div
        className="h-full bg-primary transition-[width] duration-200 motion-reduce:transition-none"
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}
