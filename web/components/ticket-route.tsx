"use client";

import { useSearchParams } from "next/navigation";
import { ErrorState } from "@/components/states";
import { TicketView } from "@/components/ticket-view";

export function TicketRoute() {
  const searchParams = useSearchParams();
  const id = searchParams.get("id")?.trim();

  if (!id) {
    return <ErrorState message="Choose a ticket from the triage queue to open its detail view." />;
  }

  return <TicketView id={id} />;
}
