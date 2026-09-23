from __future__ import annotations

from types import MappingProxyType

from .models import (
    MetricChannelDefinition,
    MetricSemantic,
    MetricValueType,
    SensorMetricDefinition,
)


MILLISECONDS = "ms"
PERCENT = "%"
BYTES = "bytes"
BITS_PER_SECOND = "bps"
DECIBEL_MILLIWATTS = "dBm"
CELSIUS = "°C"
VOLTS = "V"
MILLIAMPERES = "mA"

CANONICAL_UNITS = frozenset(
    {
        MILLISECONDS,
        PERCENT,
        BYTES,
        BITS_PER_SECOND,
        DECIBEL_MILLIWATTS,
        CELSIUS,
        VOLTS,
        MILLIAMPERES,
    }
)


LATENCY = MetricChannelDefinition(
    key="latency_ms",
    label="Latency",
    unit=MILLISECONDS,
    semantic=MetricSemantic.GAUGE,
    expected_min=0,
)
PACKET_LOSS = MetricChannelDefinition(
    key="packet_loss_pct",
    label="Packet loss",
    unit=PERCENT,
    semantic=MetricSemantic.GAUGE,
    expected_min=0,
    expected_max=100,
)
AVAILABILITY = MetricChannelDefinition(
    key="availability_pct",
    label="Availability",
    unit=PERCENT,
    semantic=MetricSemantic.GAUGE,
    expected_min=0,
    expected_max=100,
)
CPU_USAGE = MetricChannelDefinition(
    key="cpu_usage_pct",
    label="CPU usage",
    unit=PERCENT,
    semantic=MetricSemantic.GAUGE,
    expected_min=0,
    expected_max=100,
)
MEMORY_USAGE = MetricChannelDefinition(
    key="memory_usage_pct",
    label="Memory usage",
    unit=PERCENT,
    semantic=MetricSemantic.GAUGE,
    expected_min=0,
    expected_max=100,
)
MEMORY_USED = MetricChannelDefinition(
    key="memory_used_bytes",
    label="Memory used",
    unit=BYTES,
    value_type=MetricValueType.INTEGER,
    semantic=MetricSemantic.GAUGE,
    expected_min=0,
)
TRAFFIC_IN = MetricChannelDefinition(
    key="traffic_in_bps",
    label="Traffic in",
    unit=BITS_PER_SECOND,
    semantic=MetricSemantic.RATE,
    expected_min=0,
)
TRAFFIC_OUT = MetricChannelDefinition(
    key="traffic_out_bps",
    label="Traffic out",
    unit=BITS_PER_SECOND,
    semantic=MetricSemantic.RATE,
    expected_min=0,
)
UTILIZATION = MetricChannelDefinition(
    key="utilization_pct",
    label="Utilization",
    unit=PERCENT,
    semantic=MetricSemantic.GAUGE,
    expected_min=0,
    expected_max=100,
)
RX_POWER = MetricChannelDefinition(
    key="rx_power_dbm",
    label="RX optical power",
    unit=DECIBEL_MILLIWATTS,
)
TX_POWER = MetricChannelDefinition(
    key="tx_power_dbm",
    label="TX optical power",
    unit=DECIBEL_MILLIWATTS,
)
TEMPERATURE = MetricChannelDefinition(
    key="temperature_c",
    label="Temperature",
    unit=CELSIUS,
)
VOLTAGE = MetricChannelDefinition(
    key="voltage_v",
    label="Voltage",
    unit=VOLTS,
)
BIAS_CURRENT = MetricChannelDefinition(
    key="bias_current_ma",
    label="Bias current",
    unit=MILLIAMPERES,
)


CHANNEL_DEFINITIONS = MappingProxyType(
    {
        definition.key: definition
        for definition in (
            LATENCY,
            PACKET_LOSS,
            AVAILABILITY,
            CPU_USAGE,
            MEMORY_USAGE,
            MEMORY_USED,
            TRAFFIC_IN,
            TRAFFIC_OUT,
            UTILIZATION,
            RX_POWER,
            TX_POWER,
            TEMPERATURE,
            VOLTAGE,
            BIAS_CURRENT,
        )
    }
)


PING_DEFINITION = SensorMetricDefinition(
    key="ping",
    label="Ping",
    channels=(LATENCY, PACKET_LOSS, AVAILABILITY),
)
CPU_DEFINITION = SensorMetricDefinition(
    key="cpu",
    label="CPU",
    channels=(CPU_USAGE,),
)
MEMORY_DEFINITION = SensorMetricDefinition(
    key="memory",
    label="Memory",
    channels=(MEMORY_USAGE, MEMORY_USED),
)
TRAFFIC_DEFINITION = SensorMetricDefinition(
    key="traffic",
    label="Traffic",
    channels=(TRAFFIC_IN, TRAFFIC_OUT, UTILIZATION),
)
SFP_DEFINITION = SensorMetricDefinition(
    key="sfp",
    label="SFP optics",
    channels=(RX_POWER, TX_POWER, TEMPERATURE, VOLTAGE, BIAS_CURRENT),
)


SENSOR_DEFINITIONS = MappingProxyType(
    {
        definition.key: definition
        for definition in (
            PING_DEFINITION,
            CPU_DEFINITION,
            MEMORY_DEFINITION,
            TRAFFIC_DEFINITION,
            SFP_DEFINITION,
        )
    }
)


def get_channel_definition(channel_key: str) -> MetricChannelDefinition:
    return CHANNEL_DEFINITIONS[channel_key]


def get_sensor_definition(sensor_type: str) -> SensorMetricDefinition:
    return SENSOR_DEFINITIONS[sensor_type]
