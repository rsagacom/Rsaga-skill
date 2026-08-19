import './globals.css';

export const metadata = {
  title: '漫剧工作台',
  description: '小说改编、分镜、资产审核与成片流水线',
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
