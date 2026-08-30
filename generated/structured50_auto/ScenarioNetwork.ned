package structured50_auto;

import inet.common.Module;
import inet.common.scenario.ScenarioManager;
import inet.networklayer.configurator.contract.IL3NetworkConfigurator;
import inet.networklayer.configurator.contract.INetworkConfigurator;
import inet.node.ethernet.EthernetLink;
import inet.node.tsn.TsnDevice;
import inet.node.tsn.TsnSwitch;
import inet.visualizer.contract.IIntegratedVisualizer;
import tsn_fault_recovery.control.PacketIdentityRecorder;
import tsn_fault_recovery.control.ProfileSwitcher;
import tsn_fault_recovery.control.ScenarioRecoveryController;

network ScenarioNetwork extends Module
{
    parameters:
        bool hasGlobalArp = default(true);
        *.ipv4.arp.typename = default(hasGlobalArp ? "GlobalArp" : "Arp");
    submodules:
        es01: TsnDevice {
            @display("p=100,420");
        }
        es02: TsnDevice {
            @display("p=133,420");
        }
        es03: TsnDevice {
            @display("p=166,420");
        }
        es04: TsnDevice {
            @display("p=199,420");
        }
        es05: TsnDevice {
            @display("p=232,420");
        }
        es06: TsnDevice {
            @display("p=265,420");
        }
        es07: TsnDevice {
            @display("p=298,420");
        }
        es08: TsnDevice {
            @display("p=331,420");
        }
        es09: TsnDevice {
            @display("p=364,420");
        }
        es10: TsnDevice {
            @display("p=397,420");
        }
        es11: TsnDevice {
            @display("p=430,420");
        }
        es12: TsnDevice {
            @display("p=463,420");
        }
        es13: TsnDevice {
            @display("p=496,420");
        }
        es14: TsnDevice {
            @display("p=529,420");
        }
        es15: TsnDevice {
            @display("p=562,420");
        }
        es16: TsnDevice {
            @display("p=595,420");
        }
        es17: TsnDevice {
            @display("p=628,420");
        }
        es18: TsnDevice {
            @display("p=661,420");
        }
        es19: TsnDevice {
            @display("p=694,420");
        }
        es20: TsnDevice {
            @display("p=727,420");
        }
        es21: TsnDevice {
            @display("p=760,420");
        }
        es22: TsnDevice {
            @display("p=793,420");
        }
        es23: TsnDevice {
            @display("p=826,420");
        }
        es24: TsnDevice {
            @display("p=859,420");
        }
        es25: TsnDevice {
            @display("p=892,420");
        }
        sw01: TsnSwitch {
            @display("p=100,240");
        }
        sw02: TsnSwitch {
            @display("p=116,240");
        }
        sw03: TsnSwitch {
            @display("p=132,240");
        }
        sw04: TsnSwitch {
            @display("p=148,240");
        }
        sw05: TsnSwitch {
            @display("p=164,240");
        }
        sw06: TsnSwitch {
            @display("p=180,240");
        }
        sw07: TsnSwitch {
            @display("p=196,240");
        }
        sw08: TsnSwitch {
            @display("p=212,240");
        }
        sw09: TsnSwitch {
            @display("p=228,240");
        }
        sw10: TsnSwitch {
            @display("p=244,240");
        }
        sw11: TsnSwitch {
            @display("p=260,240");
        }
        sw12: TsnSwitch {
            @display("p=276,240");
        }
        sw13: TsnSwitch {
            @display("p=292,240");
        }
        sw14: TsnSwitch {
            @display("p=308,240");
        }
        sw15: TsnSwitch {
            @display("p=324,240");
        }
        sw16: TsnSwitch {
            @display("p=340,240");
        }
        sw17: TsnSwitch {
            @display("p=356,240");
        }
        sw18: TsnSwitch {
            @display("p=372,240");
        }
        sw19: TsnSwitch {
            @display("p=388,240");
        }
        sw20: TsnSwitch {
            @display("p=404,240");
        }
        sw21: TsnSwitch {
            @display("p=420,240");
        }
        sw22: TsnSwitch {
            @display("p=436,240");
        }
        sw23: TsnSwitch {
            @display("p=452,240");
        }
        sw24: TsnSwitch {
            @display("p=468,240");
        }
        sw25: TsnSwitch {
            @display("p=484,240");
        }
        sw26: TsnSwitch {
            @display("p=500,240");
        }
        sw27: TsnSwitch {
            @display("p=516,240");
        }
        sw28: TsnSwitch {
            @display("p=532,240");
        }
        sw29: TsnSwitch {
            @display("p=548,240");
        }
        sw30: TsnSwitch {
            @display("p=564,240");
        }
        sw31: TsnSwitch {
            @display("p=580,240");
        }
        sw32: TsnSwitch {
            @display("p=596,240");
        }
        sw33: TsnSwitch {
            @display("p=612,240");
        }
        sw34: TsnSwitch {
            @display("p=628,240");
        }
        sw35: TsnSwitch {
            @display("p=644,240");
        }
        sw36: TsnSwitch {
            @display("p=660,240");
        }
        sw37: TsnSwitch {
            @display("p=676,240");
        }
        sw38: TsnSwitch {
            @display("p=692,240");
        }
        sw39: TsnSwitch {
            @display("p=708,240");
        }
        sw40: TsnSwitch {
            @display("p=724,240");
        }
        sw41: TsnSwitch {
            @display("p=740,240");
        }
        sw42: TsnSwitch {
            @display("p=756,240");
        }
        sw43: TsnSwitch {
            @display("p=772,240");
        }
        sw44: TsnSwitch {
            @display("p=788,240");
        }
        sw45: TsnSwitch {
            @display("p=804,240");
        }
        sw46: TsnSwitch {
            @display("p=820,240");
        }
        sw47: TsnSwitch {
            @display("p=836,240");
        }
        sw48: TsnSwitch {
            @display("p=852,240");
        }
        sw49: TsnSwitch {
            @display("p=868,240");
        }
        sw50: TsnSwitch {
            @display("p=884,240");
        }
        visualizer: <default(firstAvailableOrEmpty("IntegratedCanvasVisualizer"))> like IIntegratedVisualizer if typename != "" { @display("p=80,70;is=s"); }
        configurator: <default("Ipv4NetworkConfigurator")> like IL3NetworkConfigurator if typename != "" { @display("p=170,70;is=s"); }
        macForwardingTableConfigurator: <default(hasGlobalArp ? "MacForwardingTableConfigurator" : "")> like INetworkConfigurator if typename != "" { @display("p=260,70;is=s"); }
        scenarioManager: ScenarioManager { @display("p=350,70;is=s"); }
        profileSwitcher: ProfileSwitcher { @display("p=440,70;is=s"); }
        scenarioRecoveryController: ScenarioRecoveryController { @display("p=530,70;is=s"); }
        packetIdentityRecorder: PacketIdentityRecorder { @display("p=620,70;is=s"); }
    connections:
        es01.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw01.ethg++; // l_es01_sw01
        es02.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw03.ethg++; // l_es02_sw03
        es03.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw05.ethg++; // l_es03_sw05
        es04.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw07.ethg++; // l_es04_sw07
        es05.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw09.ethg++; // l_es05_sw09
        es06.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw11.ethg++; // l_es06_sw11
        es07.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw13.ethg++; // l_es07_sw13
        es08.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw15.ethg++; // l_es08_sw15
        es09.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw17.ethg++; // l_es09_sw17
        es10.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw19.ethg++; // l_es10_sw19
        es11.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw21.ethg++; // l_es11_sw21
        es12.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw23.ethg++; // l_es12_sw23
        es13.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw25.ethg++; // l_es13_sw25
        es14.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw27.ethg++; // l_es14_sw27
        es15.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw29.ethg++; // l_es15_sw29
        es16.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw31.ethg++; // l_es16_sw31
        es17.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw33.ethg++; // l_es17_sw33
        es18.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw35.ethg++; // l_es18_sw35
        es19.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw37.ethg++; // l_es19_sw37
        es20.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw39.ethg++; // l_es20_sw39
        es21.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw41.ethg++; // l_es21_sw41
        es22.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw43.ethg++; // l_es22_sw43
        es23.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw45.ethg++; // l_es23_sw45
        es24.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw47.ethg++; // l_es24_sw47
        es25.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw49.ethg++; // l_es25_sw49
        sw01.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw02.ethg++; // l_sw01_sw02
        sw01.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw11.ethg++; // l_sw01_sw11
        sw01.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw13.ethg++; // l_sw01_sw13
        sw02.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw03.ethg++; // l_sw02_sw03
        sw02.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw12.ethg++; // l_sw02_sw12
        sw03.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw04.ethg++; // l_sw03_sw04
        sw03.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw13.ethg++; // l_sw03_sw13
        sw04.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw05.ethg++; // l_sw04_sw05
        sw04.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw14.ethg++; // l_sw04_sw14
        sw05.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw06.ethg++; // l_sw05_sw06
        sw05.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw15.ethg++; // l_sw05_sw15
        sw05.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw17.ethg++; // l_sw05_sw17
        sw06.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw07.ethg++; // l_sw06_sw07
        sw06.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw16.ethg++; // l_sw06_sw16
        sw07.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw08.ethg++; // l_sw07_sw08
        sw07.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw17.ethg++; // l_sw07_sw17
        sw08.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw09.ethg++; // l_sw08_sw09
        sw08.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw18.ethg++; // l_sw08_sw18
        sw09.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw10.ethg++; // l_sw09_sw10
        sw09.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw11.ethg++; // l_sw09_sw11
        sw09.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw19.ethg++; // l_sw09_sw19
        sw10.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw20.ethg++; // l_sw10_sw20
        sw11.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw12.ethg++; // l_sw11_sw12
        sw11.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw21.ethg++; // l_sw11_sw21
        sw12.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw13.ethg++; // l_sw12_sw13
        sw12.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw22.ethg++; // l_sw12_sw22
        sw12.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw24.ethg++; // l_sw12_sw24
        sw13.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw14.ethg++; // l_sw13_sw14
        sw13.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw23.ethg++; // l_sw13_sw23
        sw14.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw15.ethg++; // l_sw14_sw15
        sw14.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw24.ethg++; // l_sw14_sw24
        sw15.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw16.ethg++; // l_sw15_sw16
        sw15.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw25.ethg++; // l_sw15_sw25
        sw16.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw17.ethg++; // l_sw16_sw17
        sw16.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw26.ethg++; // l_sw16_sw26
        sw16.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw28.ethg++; // l_sw16_sw28
        sw17.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw18.ethg++; // l_sw17_sw18
        sw17.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw27.ethg++; // l_sw17_sw27
        sw18.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw19.ethg++; // l_sw18_sw19
        sw18.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw28.ethg++; // l_sw18_sw28
        sw19.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw20.ethg++; // l_sw19_sw20
        sw19.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw29.ethg++; // l_sw19_sw29
        sw20.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw22.ethg++; // l_sw20_sw22
        sw20.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw30.ethg++; // l_sw20_sw30
        sw21.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw22.ethg++; // l_sw21_sw22
        sw21.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw31.ethg++; // l_sw21_sw31
        sw22.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw23.ethg++; // l_sw22_sw23
        sw22.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw32.ethg++; // l_sw22_sw32
        sw23.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw24.ethg++; // l_sw23_sw24
        sw23.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw33.ethg++; // l_sw23_sw33
        sw23.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw35.ethg++; // l_sw23_sw35
        sw24.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw25.ethg++; // l_sw24_sw25
        sw24.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw34.ethg++; // l_sw24_sw34
        sw25.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw26.ethg++; // l_sw25_sw26
        sw25.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw35.ethg++; // l_sw25_sw35
        sw26.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw27.ethg++; // l_sw26_sw27
        sw26.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw36.ethg++; // l_sw26_sw36
        sw27.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw28.ethg++; // l_sw27_sw28
        sw27.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw37.ethg++; // l_sw27_sw37
        sw27.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw39.ethg++; // l_sw27_sw39
        sw28.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw29.ethg++; // l_sw28_sw29
        sw28.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw38.ethg++; // l_sw28_sw38
        sw29.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw30.ethg++; // l_sw29_sw30
        sw29.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw39.ethg++; // l_sw29_sw39
        sw30.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw40.ethg++; // l_sw30_sw40
        sw31.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw32.ethg++; // l_sw31_sw32
        sw31.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw41.ethg++; // l_sw31_sw41
        sw32.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw33.ethg++; // l_sw32_sw33
        sw32.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw42.ethg++; // l_sw32_sw42
        sw33.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw34.ethg++; // l_sw33_sw34
        sw33.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw43.ethg++; // l_sw33_sw43
        sw34.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw35.ethg++; // l_sw34_sw35
        sw34.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw44.ethg++; // l_sw34_sw44
        sw34.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw46.ethg++; // l_sw34_sw46
        sw35.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw36.ethg++; // l_sw35_sw36
        sw35.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw45.ethg++; // l_sw35_sw45
        sw36.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw37.ethg++; // l_sw36_sw37
        sw36.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw46.ethg++; // l_sw36_sw46
        sw37.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw38.ethg++; // l_sw37_sw38
        sw37.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw47.ethg++; // l_sw37_sw47
        sw38.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw39.ethg++; // l_sw38_sw39
        sw38.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw48.ethg++; // l_sw38_sw48
        sw38.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw50.ethg++; // l_sw38_sw50
        sw39.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw40.ethg++; // l_sw39_sw40
        sw39.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw49.ethg++; // l_sw39_sw49
        sw40.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw50.ethg++; // l_sw40_sw50
        sw41.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw42.ethg++; // l_sw41_sw42
        sw42.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw43.ethg++; // l_sw42_sw43
        sw43.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw44.ethg++; // l_sw43_sw44
        sw44.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw45.ethg++; // l_sw44_sw45
        sw45.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw46.ethg++; // l_sw45_sw46
        sw46.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw47.ethg++; // l_sw46_sw47
        sw47.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw48.ethg++; // l_sw47_sw48
        sw48.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw49.ethg++; // l_sw48_sw49
        sw49.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw50.ethg++; // l_sw49_sw50
}
