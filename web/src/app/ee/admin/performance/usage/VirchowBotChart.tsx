import { ThreeDotsLoader } from "@/components/Loading";
import { getDatesList, useVirchowBotAnalytics } from "../lib";
import { DateRangePickerValue } from "@/components/dateRangeSelectors/AdminDateRangeSelector";
import Text from "@/components/ui/text";
import Title from "@/components/ui/title";
import CardSection from "@/components/admin/CardSection";
import { AreaChartDisplay } from "@/components/ui/areaChart";

export function VirchowBotChart({
  timeRange,
}: {
  timeRange: DateRangePickerValue;
}) {
  const {
    data: virchowBotAnalyticsData,
    isLoading: isVirchowBotAnalyticsLoading,
    error: virchowBotAnalyticsError,
  } = useVirchowBotAnalytics(timeRange);

  let chart;
  if (isVirchowBotAnalyticsLoading) {
    chart = (
      <div className="h-80 flex flex-col">
        <ThreeDotsLoader />
      </div>
    );
  } else if (
    !virchowBotAnalyticsData ||
    virchowBotAnalyticsData[0] == undefined ||
    virchowBotAnalyticsError
  ) {
    chart = (
      <div className="h-80 text-red-600 text-bold flex flex-col">
        <p className="m-auto">Failed to fetch feedback data...</p>
      </div>
    );
  } else {
    const initialDate =
      timeRange.from || new Date(virchowBotAnalyticsData[0].date);
    const dateRange = getDatesList(initialDate);

    const dateToVirchowBotAnalytics = new Map(
      virchowBotAnalyticsData.map((virchowBotAnalyticsEntry) => [
        virchowBotAnalyticsEntry.date,
        virchowBotAnalyticsEntry,
      ])
    );

    chart = (
      <AreaChartDisplay
        className="mt-4"
        data={dateRange.map((dateStr) => {
          const virchowBotAnalyticsForDate = dateToVirchowBotAnalytics.get(dateStr);
          return {
            Day: dateStr,
            "Total Queries": virchowBotAnalyticsForDate?.total_queries || 0,
            "Automatically Resolved":
              virchowBotAnalyticsForDate?.auto_resolved || 0,
          };
        })}
        categories={["Total Queries", "Automatically Resolved"]}
        index="Day"
        colors={["indigo", "fuchsia"]}
        yAxisWidth={60}
      />
    );
  }

  return (
    <CardSection className="mt-8">
      <Title>Virchow Bot</Title>
      <Text>Total Queries vs Auto Resolved</Text>
      {chart}
    </CardSection>
  );
}
