"use client";

import { useCallback, useEffect, useState } from "react";

interface Department {
  id: string;
  name: string;
}

interface Grant {
  id: string;
  granting_dept_id: string;
  granting_dept_name: string;
  receiving_dept_id: string;
  receiving_dept_name: string;
  access_type: string;
  created_at?: string;
  expires_at?: string | null;
}

export default function DepartmentPermissionsPage() {
  const [departments, setDepartments] = useState<Department[]>([]);
  const [grants, setGrants] = useState<Grant[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [granting, setGranting] = useState("");
  const [receiving, setReceiving] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [deptRes, grantRes] = await Promise.all([
        fetch("/api/auth/departments", { credentials: "include" }),
        fetch("/api/admin/dept-grants", { credentials: "include" }),
      ]);
      if (!deptRes.ok) throw new Error(`Departments: ${deptRes.status}`);
      if (!grantRes.ok) throw new Error(`Grants: ${grantRes.status}`);
      const deptData = await deptRes.json();
      const grantData = await grantRes.json();
      setDepartments(Array.isArray(deptData) ? deptData : []);
      setGrants(Array.isArray(grantData) ? grantData : []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const onCreate = async () => {
    if (!granting || !receiving || granting === receiving) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch("/api/admin/dept-grants", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          granting_dept_id: granting,
          receiving_dept_id: receiving,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `HTTP ${res.status}`);
      }
      setGranting("");
      setReceiving("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create grant");
    } finally {
      setSubmitting(false);
    }
  };

  const onDelete = async (id: string) => {
    if (!confirm("Remove this access grant?")) return;
    setError(null);
    try {
      const res = await fetch(`/api/admin/dept-grants/${id}`, {
        method: "DELETE",
        credentials: "include",
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `HTTP ${res.status}`);
      }
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete grant");
    }
  };

  return (
    <div className="px-6 py-6 max-w-5xl mx-auto">
      <h1 className="text-xl font-semibold mb-2">Department Permissions</h1>
      <p className="text-sm text-text-500 mb-6">
        Give one department read access to another department's documents and
        chunks. Useful when your documents are owned by one department (e.g.
        <span className="font-semibold"> Default</span>) but users in another
        department (e.g.
        <span className="font-semibold"> Sales</span>) need to search them.
      </p>

      {error && (
        <div className="mb-4 rounded-md border border-red-300 bg-red-50 text-red-700 text-sm p-3">
          {error}
        </div>
      )}

      {/* ── Add grant form ────────────────────────────────────────────────── */}
      <div className="mb-8 rounded-lg border border-border-200 bg-background-tint-00 p-4">
        <div className="text-sm font-semibold mb-3">Grant new read access</div>
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col min-w-[180px]">
            <label className="text-xs text-text-500 mb-1">
              Source (department whose docs become readable)
            </label>
            <select
              value={granting}
              onChange={(e) => setGranting(e.target.value)}
              className="border border-border-300 rounded-md px-2 py-2 text-sm bg-white"
              disabled={loading || submitting}
            >
              <option value="">— select —</option>
              {departments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </div>

          <div className="text-text-400 pb-2.5">→</div>

          <div className="flex flex-col min-w-[180px]">
            <label className="text-xs text-text-500 mb-1">
              Target (department whose users gain access)
            </label>
            <select
              value={receiving}
              onChange={(e) => setReceiving(e.target.value)}
              className="border border-border-300 rounded-md px-2 py-2 text-sm bg-white"
              disabled={loading || submitting}
            >
              <option value="">— select —</option>
              {departments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </div>

          <button
            type="button"
            onClick={onCreate}
            disabled={!granting || !receiving || granting === receiving || submitting}
            className="ml-auto px-4 py-2 rounded-md bg-accent text-accent-foreground text-sm font-medium disabled:opacity-40 hover:opacity-90"
          >
            {submitting ? "Granting…" : "Grant read access"}
          </button>
        </div>
        {granting && receiving && granting === receiving && (
          <div className="mt-2 text-xs text-amber-700">
            A department cannot grant access to itself.
          </div>
        )}
      </div>

      {/* ── Existing grants table ─────────────────────────────────────────── */}
      <div className="text-sm font-semibold mb-2">Current grants</div>
      {loading ? (
        <div className="text-sm text-text-500">Loading…</div>
      ) : grants.length === 0 ? (
        <div className="text-sm text-text-500 border border-dashed border-border-300 rounded-md p-6 text-center">
          No active grants. Add one above so users outside a department can
          search its documents.
        </div>
      ) : (
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="text-left text-text-500 border-b border-border-200">
              <th className="py-2 pr-3 font-medium">Source (granting)</th>
              <th className="py-2 pr-3 font-medium">Target (receiving)</th>
              <th className="py-2 pr-3 font-medium">Access</th>
              <th className="py-2 pr-3 font-medium">Created</th>
              <th className="py-2 pr-3 font-medium"></th>
            </tr>
          </thead>
          <tbody>
            {grants.map((g) => (
              <tr key={g.id} className="border-b border-border-100">
                <td className="py-2 pr-3">{g.granting_dept_name}</td>
                <td className="py-2 pr-3">{g.receiving_dept_name}</td>
                <td className="py-2 pr-3 uppercase text-xs text-text-500">
                  {g.access_type}
                </td>
                <td className="py-2 pr-3 text-xs text-text-500">
                  {g.created_at?.slice(0, 10)}
                </td>
                <td className="py-2 pr-3 text-right">
                  <button
                    type="button"
                    onClick={() => onDelete(g.id)}
                    className="text-xs text-red-600 hover:underline"
                  >
                    Revoke
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
