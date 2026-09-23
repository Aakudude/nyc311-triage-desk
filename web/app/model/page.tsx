import type { Metadata } from "next";
import { ModelTrustView } from "@/components/model-trust-view";

export const metadata: Metadata = { title: "Model trust" };

export default function ModelPage() {
  return <ModelTrustView />;
}
