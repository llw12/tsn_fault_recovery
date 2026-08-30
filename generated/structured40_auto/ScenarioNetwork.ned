package structured40_auto;

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
            @display("p=142,420");
        }
        es03: TsnDevice {
            @display("p=184,420");
        }
        es04: TsnDevice {
            @display("p=226,420");
        }
        es05: TsnDevice {
            @display("p=268,420");
        }
        es06: TsnDevice {
            @display("p=310,420");
        }
        es07: TsnDevice {
            @display("p=352,420");
        }
        es08: TsnDevice {
            @display("p=394,420");
        }
        es09: TsnDevice {
            @display("p=436,420");
        }
        es10: TsnDevice {
            @display("p=478,420");
        }
        es11: TsnDevice {
            @display("p=520,420");
        }
        es12: TsnDevice {
            @display("p=562,420");
        }
        es13: TsnDevice {
            @display("p=604,420");
        }
        es14: TsnDevice {
            @display("p=646,420");
        }
        es15: TsnDevice {
            @display("p=688,420");
        }
        es16: TsnDevice {
            @display("p=730,420");
        }
        es17: TsnDevice {
            @display("p=772,420");
        }
        es18: TsnDevice {
            @display("p=814,420");
        }
        es19: TsnDevice {
            @display("p=856,420");
        }
        es20: TsnDevice {
            @display("p=898,420");
        }
        sw01: TsnSwitch {
            @display("p=100,240");
        }
        sw02: TsnSwitch {
            @display("p=120,240");
        }
        sw03: TsnSwitch {
            @display("p=140,240");
        }
        sw04: TsnSwitch {
            @display("p=160,240");
        }
        sw05: TsnSwitch {
            @display("p=180,240");
        }
        sw06: TsnSwitch {
            @display("p=200,240");
        }
        sw07: TsnSwitch {
            @display("p=220,240");
        }
        sw08: TsnSwitch {
            @display("p=240,240");
        }
        sw09: TsnSwitch {
            @display("p=260,240");
        }
        sw10: TsnSwitch {
            @display("p=280,240");
        }
        sw11: TsnSwitch {
            @display("p=300,240");
        }
        sw12: TsnSwitch {
            @display("p=320,240");
        }
        sw13: TsnSwitch {
            @display("p=340,240");
        }
        sw14: TsnSwitch {
            @display("p=360,240");
        }
        sw15: TsnSwitch {
            @display("p=380,240");
        }
        sw16: TsnSwitch {
            @display("p=400,240");
        }
        sw17: TsnSwitch {
            @display("p=420,240");
        }
        sw18: TsnSwitch {
            @display("p=440,240");
        }
        sw19: TsnSwitch {
            @display("p=460,240");
        }
        sw20: TsnSwitch {
            @display("p=480,240");
        }
        sw21: TsnSwitch {
            @display("p=500,240");
        }
        sw22: TsnSwitch {
            @display("p=520,240");
        }
        sw23: TsnSwitch {
            @display("p=540,240");
        }
        sw24: TsnSwitch {
            @display("p=560,240");
        }
        sw25: TsnSwitch {
            @display("p=580,240");
        }
        sw26: TsnSwitch {
            @display("p=600,240");
        }
        sw27: TsnSwitch {
            @display("p=620,240");
        }
        sw28: TsnSwitch {
            @display("p=640,240");
        }
        sw29: TsnSwitch {
            @display("p=660,240");
        }
        sw30: TsnSwitch {
            @display("p=680,240");
        }
        sw31: TsnSwitch {
            @display("p=700,240");
        }
        sw32: TsnSwitch {
            @display("p=720,240");
        }
        sw33: TsnSwitch {
            @display("p=740,240");
        }
        sw34: TsnSwitch {
            @display("p=760,240");
        }
        sw35: TsnSwitch {
            @display("p=780,240");
        }
        sw36: TsnSwitch {
            @display("p=800,240");
        }
        sw37: TsnSwitch {
            @display("p=820,240");
        }
        sw38: TsnSwitch {
            @display("p=840,240");
        }
        sw39: TsnSwitch {
            @display("p=860,240");
        }
        sw40: TsnSwitch {
            @display("p=880,240");
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
        sw01.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw02.ethg++; // l_sw01_sw02
        sw01.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw09.ethg++; // l_sw01_sw09
        sw01.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw11.ethg++; // l_sw01_sw11
        sw02.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw03.ethg++; // l_sw02_sw03
        sw02.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw10.ethg++; // l_sw02_sw10
        sw03.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw04.ethg++; // l_sw03_sw04
        sw03.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw11.ethg++; // l_sw03_sw11
        sw04.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw05.ethg++; // l_sw04_sw05
        sw04.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw12.ethg++; // l_sw04_sw12
        sw05.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw06.ethg++; // l_sw05_sw06
        sw05.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw13.ethg++; // l_sw05_sw13
        sw05.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw15.ethg++; // l_sw05_sw15
        sw06.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw07.ethg++; // l_sw06_sw07
        sw06.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw14.ethg++; // l_sw06_sw14
        sw07.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw08.ethg++; // l_sw07_sw08
        sw07.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw15.ethg++; // l_sw07_sw15
        sw08.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw16.ethg++; // l_sw08_sw16
        sw09.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw10.ethg++; // l_sw09_sw10
        sw09.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw17.ethg++; // l_sw09_sw17
        sw10.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw11.ethg++; // l_sw10_sw11
        sw10.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw18.ethg++; // l_sw10_sw18
        sw10.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw20.ethg++; // l_sw10_sw20
        sw11.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw12.ethg++; // l_sw11_sw12
        sw11.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw19.ethg++; // l_sw11_sw19
        sw12.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw13.ethg++; // l_sw12_sw13
        sw12.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw20.ethg++; // l_sw12_sw20
        sw13.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw14.ethg++; // l_sw13_sw14
        sw13.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw21.ethg++; // l_sw13_sw21
        sw14.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw15.ethg++; // l_sw14_sw15
        sw14.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw22.ethg++; // l_sw14_sw22
        sw14.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw24.ethg++; // l_sw14_sw24
        sw15.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw16.ethg++; // l_sw15_sw16
        sw15.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw23.ethg++; // l_sw15_sw23
        sw16.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw24.ethg++; // l_sw16_sw24
        sw17.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw18.ethg++; // l_sw17_sw18
        sw17.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw25.ethg++; // l_sw17_sw25
        sw18.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw19.ethg++; // l_sw18_sw19
        sw18.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw26.ethg++; // l_sw18_sw26
        sw19.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw20.ethg++; // l_sw19_sw20
        sw19.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw27.ethg++; // l_sw19_sw27
        sw19.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw29.ethg++; // l_sw19_sw29
        sw20.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw21.ethg++; // l_sw20_sw21
        sw20.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw28.ethg++; // l_sw20_sw28
        sw21.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw22.ethg++; // l_sw21_sw22
        sw21.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw29.ethg++; // l_sw21_sw29
        sw22.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw23.ethg++; // l_sw22_sw23
        sw22.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw30.ethg++; // l_sw22_sw30
        sw23.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw24.ethg++; // l_sw23_sw24
        sw23.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw25.ethg++; // l_sw23_sw25
        sw23.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw31.ethg++; // l_sw23_sw31
        sw24.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw32.ethg++; // l_sw24_sw32
        sw25.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw26.ethg++; // l_sw25_sw26
        sw25.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw33.ethg++; // l_sw25_sw33
        sw26.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw27.ethg++; // l_sw26_sw27
        sw26.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw34.ethg++; // l_sw26_sw34
        sw27.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw28.ethg++; // l_sw27_sw28
        sw27.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw35.ethg++; // l_sw27_sw35
        sw28.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw29.ethg++; // l_sw28_sw29
        sw28.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw36.ethg++; // l_sw28_sw36
        sw28.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw38.ethg++; // l_sw28_sw38
        sw29.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw30.ethg++; // l_sw29_sw30
        sw29.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw37.ethg++; // l_sw29_sw37
        sw30.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw31.ethg++; // l_sw30_sw31
        sw30.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw38.ethg++; // l_sw30_sw38
        sw31.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw32.ethg++; // l_sw31_sw32
        sw31.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw39.ethg++; // l_sw31_sw39
        sw32.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw34.ethg++; // l_sw32_sw34
        sw32.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw40.ethg++; // l_sw32_sw40
        sw33.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw34.ethg++; // l_sw33_sw34
        sw34.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw35.ethg++; // l_sw34_sw35
        sw35.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw36.ethg++; // l_sw35_sw36
        sw36.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw37.ethg++; // l_sw36_sw37
        sw37.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw38.ethg++; // l_sw37_sw38
        sw38.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw39.ethg++; // l_sw38_sw39
        sw39.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw40.ethg++; // l_sw39_sw40
}
