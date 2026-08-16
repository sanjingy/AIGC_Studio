import * as React from "react";

import { cn } from "@/lib/utils";

export interface FieldProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label: string;
  hint?: string;
  error?: string;
}

/**
 * 表单字段。三条硬规则：
 * 1. 标签常驻可见——placeholder 当标签，用户一开始输入就忘了这栏填什么
 * 2. 错误紧贴字段——不做顶部错误汇总
 * 3. aria-invalid + aria-describedby——读屏能念出错误原因
 */
export function Field({ label, hint, error, id, className, ...props }: FieldProps) {
  const autoId = React.useId();
  const fieldId = id ?? autoId;
  const hintId = `${fieldId}-hint`;
  const errorId = `${fieldId}-error`;

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={fieldId} className="text-sm font-medium text-fg">
        {label}
      </label>
      <input
        id={fieldId}
        aria-invalid={error ? true : undefined}
        aria-describedby={cn(hint && hintId, error && errorId) || undefined}
        className={cn(
          "h-9 rounded-md border bg-surface px-2.5 text-base text-fg",
          "placeholder:text-fg-subtle",
          "transition-colors duration-150",
          error ? "border-danger" : "border-border-strong hover:border-fg-subtle",
          className,
        )}
        {...props}
      />
      {hint && !error && (
        <p id={hintId} className="text-xs text-fg-subtle">
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} role="alert" className="text-xs text-danger">
          {error}
        </p>
      )}
    </div>
  );
}
