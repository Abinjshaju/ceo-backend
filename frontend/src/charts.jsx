import { ResponsiveContainer, BarChart, Bar, LineChart, Line, PieChart, Pie, Cell, XAxis, YAxis, CartesianGrid, Tooltip, Legend } from 'recharts';
const COLORS = ['#7668ed', '#56c5c2', '#f0ae78', '#ea7f9a', '#5384df', '#4cba89', '#b48ae8', '#e2b34c'];
const number = (value) => (typeof value === 'number' ? value.toLocaleString(undefined, { maximumFractionDigits: 2 }) : value);

export default function ChartArtifact({ data }) {
  const series = data.series || [];
  const labels = [...new Set(series.flatMap((item) => item.points.map((point) => point.x)))];
  const rows = labels.map((label) => Object.fromEntries([['label', label], ...series.map((item, index) => [
    `series${index}`, item.points.find((point) => point.x === label)?.y ?? null,
  ])]));
  const common = <><CartesianGrid strokeDasharray="3 3" vertical={false}/>
    <XAxis dataKey="label" tick={{ fontSize: 11 }} height={55} label={data.x_label ? { value: data.x_label, position: 'insideBottom' } : undefined}/>
    <YAxis width={72} tick={{ fontSize: 11 }} tickFormatter={number} label={data.y_label ? { value: data.y_label, angle: -90, position: 'insideLeft' } : undefined}/>
    <Tooltip formatter={(value) => `${number(value)}${data.unit ? ` ${data.unit}` : ''}`}/><Legend/></>;
  return <div className="responsive-chart" aria-label={`${data.chart_type} chart`}>
    <ResponsiveContainer width="100%" height={320} minWidth={0}>
      {data.chart_type === 'pie' ? <PieChart><Pie data={series[0]?.points || []} dataKey="y" nameKey="x" innerRadius="45%" outerRadius="75%">
        {(series[0]?.points || []).map((point, index) => <Cell key={point.x} fill={COLORS[index % COLORS.length]}/>)}</Pie><Tooltip formatter={number}/><Legend/></PieChart>
        : data.chart_type === 'line' ? <LineChart data={rows} margin={{ top: 16, right: 20, bottom: 8, left: 8 }}>{common}
          {series.map((item, index) => <Line key={index} dataKey={`series${index}`} name={item.name} stroke={COLORS[index % COLORS.length]} strokeWidth={2} connectNulls={false} isAnimationActive={false}/>)}</LineChart>
          : <BarChart data={rows} margin={{ top: 16, right: 20, bottom: 8, left: 8 }}>{common}
            {series.map((item, index) => <Bar key={index} dataKey={`series${index}`} name={item.name} fill={COLORS[index % COLORS.length]} radius={[4, 4, 0, 0]} isAnimationActive={false}/>)}</BarChart>}
    </ResponsiveContainer>
    <details className="chart-data"><summary>View chart data</summary><div className="artifact-table-wrap"><table className="artifact-table"><thead><tr><th>{data.x_label || 'Category'}</th>{series.map((item, index) => <th key={index}>{item.name}</th>)}</tr></thead><tbody>{rows.map((row) => <tr key={row.label}><td>{row.label}</td>{series.map((_, index) => <td key={index}>{row[`series${index}`] === null ? '—' : number(row[`series${index}`])}</td>)}</tr>)}</tbody></table></div></details>
  </div>;
}
