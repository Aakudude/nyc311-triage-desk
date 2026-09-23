"use client";

import Image from "next/image";
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
        <Link href="/" className="brand-block" aria-label="311 Triage Desk home">
          <Image
            src="/311-triage-logo.png"
            alt="311 Triage Desk"
            width={1368}
            height={768}
            className="brand-logo"
            priority
            unoptimized
          />
        </Link>
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
          <Link href="/" className="mobile-brand" aria-label="311 Triage Desk home">
            <Image src="/311-triage-logo.png" alt="311 Triage Desk" width={1368} height={768} unoptimized />
          </Link>
          <div className="topbar-context"><span>NYC 311</span><strong>Supervisor workspace</strong></div>
          <a href="https://nyc311-triage-api.onrender.com/docs" target="_blank" rel="noreferrer" className="api-link">API docs ↗</a>
        </header>
        <main className="content">{children}</main>
      </div>
      {open && <button className="nav-scrim" onClick={() => setOpen(false)} aria-label="Close navigation" />}
    </div>
  );
}
