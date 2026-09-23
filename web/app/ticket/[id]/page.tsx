import type { Metadata } from "next";
import { TicketView } from "@/components/ticket-view";

export const metadata: Metadata = { title: "Ticket detail" };

export default async function TicketPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <TicketView id={id} />;
}
