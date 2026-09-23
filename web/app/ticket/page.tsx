import type { Metadata } from "next";
import { Suspense } from "react";
import { TicketRoute } from "@/components/ticket-route";
import { LoadingState } from "@/components/states";

export const metadata: Metadata = { title: "Ticket detail" };

export default function TicketPage() {
  return (
    <Suspense fallback={<LoadingState />}>
      <TicketRoute />
    </Suspense>
  );
}
