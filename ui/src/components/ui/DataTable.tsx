"use client";

export interface Column<T> {
  key: string;
  label: string;
  render?: (row: T) => React.ReactNode;
  align?: "left" | "right";
  width?: string;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  emptyMessage?: string;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function DataTable<T extends Record<string, any>>({
  columns,
  rows,
  emptyMessage = "No data",
}: DataTableProps<T>) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12px]">
        <thead>
          <tr className="border-b border-grim-border">
            {columns.map((col) => (
              <th
                key={col.key}
                className={`py-2 px-3 text-grim-text-dim font-medium uppercase tracking-wider ${
                  col.align === "right" ? "text-right" : "text-left"
                }`}
                style={col.width ? { width: col.width } : undefined}
              >
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td
                colSpan={columns.length}
                className="py-6 text-center text-grim-text-dim"
              >
                {emptyMessage}
              </td>
            </tr>
          ) : (
            rows.map((row, i) => (
              <tr
                key={i}
                className="border-b border-grim-border/50 hover:bg-grim-surface-hover transition-colors"
              >
                {columns.map((col) => (
                  <td
                    key={col.key}
                    className={`py-2 px-3 text-grim-text ${
                      col.align === "right" ? "text-right" : "text-left"
                    }`}
                  >
                    {col.render
                      ? col.render(row)
                      : String(row[col.key] ?? "")}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
