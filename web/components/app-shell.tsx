"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

const NAV = [
  { href: "/", label: "Overview", code: "01" },
  { href: "/queue", label: "Triage queue", code: "02" },
  { href: "/hotspots", label: "Hotspots", code: "03" },
  { href: "/model", label: "Model trust", code: "04" },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  return (
    <div className="app-frame">
      <aside className={`sidebar ${open ? "sidebar-open" : ""}`}>
        <div className="brand-block">
          <div className="brand-mark" aria-hidden="true"><span>3</span><span>1</span><span>1</span></div>
          <div>
            <strong>Triage Desk</strong>
            <small>NYC operations replay</small>
          </div>
        </div>
        <nav aria-label="Primary navigation">
          {NAV.map((item) => {
            const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={active ? "nav-link nav-active" : "nav-link"}
                onClick={() => setOpen(false)}
              >
                <span>{item.code}</span>{item.label}
              </Link>
            );
          })}
        </nav>
        <div className="sidebar-foot">
          <span className="status-dot" />
          <div><strong>Model v1</strong><small>Historical replay</small></div>
        </div>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <button className="menu-button" onClick={() => setOpen((value) => !value)} aria-label="Toggle navigation">Menu</button>
          <div className="topbar-context"><span>NYC 311</span><strong>Supervisor workspace</strong></div>
          <a href="https://nyc311-triage-api.onrender.com/docs" target="_blank" rel="noreferrer" className="api-link">API docs ↗</a>
        </header>
        <main className="content">{children}</main>
      </div>
      {open && <button className="nav-scrim" onClick={() => setOpen(false)} aria-label="Close navigation" />}
    </div>
  );
}
