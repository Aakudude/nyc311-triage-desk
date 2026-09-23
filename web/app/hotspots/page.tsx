import type { Metadata } from "next";
import { HotspotsView } from "@/components/hotspots-view";

export const metadata: Metadata = { title: "Breach hotspots" };

export default function HotspotsPage() {
  return <HotspotsView />;
}
