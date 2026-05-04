/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

import React from 'react';
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  flexRender,
  type ColumnDef,
  type SortingState,
  type ColumnFiltersState,
  type OnChangeFn,
} from '@tanstack/react-table';
import clsx from 'clsx';

type TableDensity = 'comfortable' | 'compact';

interface DataTableProps<TData> {
  columns: ColumnDef<TData>[];
  data: TData[];
  sorting?: SortingState;
  onSortingChange?: OnChangeFn<SortingState>;
  columnFilters?: ColumnFiltersState;
  onColumnFiltersChange?: OnChangeFn<ColumnFiltersState>;
  onRowClick?: (row: TData) => void;
  density?: TableDensity;
  emptyState?: React.ReactNode;
  className?: string;
}

const rowHeightClasses: Record<TableDensity, string> = {
  comfortable: 'py-nkz-inline',
  compact: 'py-1',
};

export function DataTable<TData extends Record<string, unknown>>({
  columns,
  data,
  sorting,
  onSortingChange,
  columnFilters,
  onColumnFiltersChange,
  onRowClick,
  density = 'comfortable',
  emptyState,
  className,
}: DataTableProps<TData>) {
  const [internalSorting, setInternalSorting] = React.useState<SortingState>([]);
  const [internalFilters, setInternalFilters] = React.useState<ColumnFiltersState>([]);

  const table = useReactTable({
    data,
    columns,
    state: {
      sorting: sorting ?? internalSorting,
      columnFilters: columnFilters ?? internalFilters,
    },
    onSortingChange: onSortingChange ?? setInternalSorting,
    onColumnFiltersChange: onColumnFiltersChange ?? setInternalFilters,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
  });

  if (data.length === 0 && emptyState) {
    return <>{emptyState}</>;
  }

  return (
    <div className={clsx('overflow-x-auto', className)}>
      <table className="w-full border-collapse">
        <thead>
          {table.getHeaderGroups().map((headerGroup) => (
            <tr key={headerGroup.id}>
              {headerGroup.headers.map((header) => (
                <th
                  key={header.id}
                  className={clsx(
                    'text-left text-nkz-xs font-semibold text-nkz-text-secondary uppercase tracking-wider',
                    'px-nkz-stack py-nkz-inline border-b border-nkz-border',
                    header.column.getCanSort() && 'cursor-pointer select-none hover:text-nkz-text-primary'
                  )}
                  onClick={header.column.getToggleSortingHandler()}
                >
                  <span className="inline-flex items-center gap-nkz-tight">
                    {flexRender(header.column.columnDef.header, header.getContext())}
                    {{
                      asc: <span className="text-nkz-accent-base">{'▲'}</span>,
                      desc: <span className="text-nkz-accent-base">{'▼'}</span>,
                    }[header.column.getIsSorted() as string] ?? null}
                  </span>
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr
              key={row.id}
              className={clsx(
                'border-b border-nkz-border transition-colors duration-nkz-fast',
                onRowClick && 'cursor-pointer hover:bg-nkz-surface-sunken'
              )}
              onClick={() => onRowClick?.(row.original)}
            >
              {row.getVisibleCells().map((cell) => (
                <td
                  key={cell.id}
                  className={clsx(
                    'px-nkz-stack text-nkz-sm text-nkz-text-primary',
                    rowHeightClasses[density]
                  )}
                >
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
