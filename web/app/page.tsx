import type { Metadata } from "next";
import { OverviewDashboard } from "@/components/overview-dashboard";

export const metadata: Metadata = { title: "Operations overview" };

export default function OverviewPage() {
  return <OverviewDashboard />;
}
