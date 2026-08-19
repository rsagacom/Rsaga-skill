import Link from "next/link";

type BillingReturnPageProps = {
  searchParams: Promise<{ order_id?: string | string[] }>;
};

export default async function BillingSuccessPage({ searchParams }: BillingReturnPageProps) {
  const params = await searchParams;
  const rawOrderId = params.order_id;
  const orderId = Array.isArray(rawOrderId) ? rawOrderId[0] : rawOrderId;
  const returnHref = orderId
    ? `/?billing=success&order_id=${encodeURIComponent(orderId)}`
    : "/?billing=success";
  return (
    <main className="billing-return">
      <section className="billing-return-card">
        <span className="eyebrow">PAYMENT RETURN</span>
        <h1>支付页面已返回</h1>
        <p>订单是否入账以支付 Webhook 验证为准。返回工作台后，订单状态和积分流水会自动刷新。</p>
        <Link className="primary-button" href={returnHref}>
          返回工作台
        </Link>
      </section>
    </main>
  );
}
