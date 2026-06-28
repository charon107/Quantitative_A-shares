import { useState } from "react";
import type { ReactNode } from "react";
import { useCompanyInfo } from "../api/client";
import { Card, CardHeader } from "./Card";

function Field({ label, value }: { label: string; value: ReactNode }) {
  if (value == null || value === "") return null;
  return (
    <div>
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-0.5 text-sm font-medium text-ink">{value}</div>
    </div>
  );
}

function fmtCapital(v: number | null): string | null {
  if (v == null) return null;
  return v >= 10000 ? `${(v / 10000).toFixed(2)} 亿元` : `${v.toLocaleString("zh-CN")} 万元`;
}

function fmtEmployees(v: number | null): string | null {
  if (v == null) return null;
  return v >= 10000 ? `${(v / 10000).toFixed(1)} 万人` : `${v.toLocaleString("zh-CN")} 人`;
}

export function CompanyInfoPanel({ code }: { code: string }) {
  const q = useCompanyInfo(code);
  const [showFull, setShowFull] = useState(false);

  if (q.isLoading || q.error || !q.data) return null; // 无公司信息时静默隐藏
  const c = q.data;

  return (
    <Card>
      <CardHeader title="公司信息" subtitle={c.fullname ?? undefined} />
      <div className="card-pad space-y-5">
        <div className="grid grid-cols-2 gap-x-6 gap-y-4 md:grid-cols-4">
          <Field label="所属行业" value={c.industry} />
          <Field label="地域" value={c.area} />
          <Field label="市场" value={c.market} />
          <Field label="上市日期" value={c.list_date} />
          <Field label="注册资本" value={fmtCapital(c.reg_capital)} />
          <Field label="员工人数" value={fmtEmployees(c.employees)} />
          <Field label="董事长" value={c.chairman} />
          <Field label="董秘" value={c.secretary} />
          <Field label="注册地" value={[c.province, c.city].filter(Boolean).join(" · ") || null} />
          <Field
            label="官网"
            value={
              c.website ? (
                <a href={/^https?:/.test(c.website) ? c.website : `http://${c.website}`} target="_blank" rel="noreferrer" className="text-clay hover:underline">
                  {c.website}
                </a>
              ) : null
            }
          />
        </div>

        {c.main_business && (
          <div>
            <div className="text-xs text-muted">主营业务</div>
            <p className="mt-1 text-sm leading-relaxed text-ink">{c.main_business}</p>
          </div>
        )}

        {c.introduction && (
          <div>
            <div className="text-xs text-muted">公司简介</div>
            <p className={`mt-1 text-sm leading-relaxed text-ink/90 ${showFull ? "" : "line-clamp-3"}`}>
              {c.introduction}
            </p>
            {c.introduction.length > 120 && (
              <button onClick={() => setShowFull((v) => !v)} className="mt-1 text-xs text-clay hover:underline">
                {showFull ? "收起" : "展开全文"}
              </button>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}
