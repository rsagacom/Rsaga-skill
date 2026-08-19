import Link from "next/link";

type BillingReturnPageProps = {
  searchParams: Promise<{ order_id?: string | string[] }>;
};

export default async function BillingCancelPage({ searchParams }: BillingReturnPageProps) {
  const params = await searchParams;
  const rawOrderId = params.order_id;
  const orderId = Array.isArray(rawOrderId) ? rawOrderId[0] : rawOrderId;
  const returnHref = orderId
    ? `/?billing=cancelled&order_id=${encodeURIComponent(orderId)}`
    : "/?billing=cancelled";
  return (
    <main className="billing-return">
      <section className="billing-return-card">
        <span className="eyebrow">PAYMENT RETURN</span>
        <h1>支付未完成</h1>
        <p>本次 Checkout 尚未完成支付，订单不会入账。你可以返回工作台重新选择订单。</p>
        <Link className="secondary-button" href={returnHref}>
          返回工作台
        </Link>
      </section>
    </main>
  );
}
