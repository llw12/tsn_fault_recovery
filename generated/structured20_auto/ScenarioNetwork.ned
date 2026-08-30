package structured20_auto;

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
            @display("p=188,420");
        }
        es03: TsnDevice {
            @display("p=276,420");
        }
        es04: TsnDevice {
            @display("p=364,420");
        }
        es05: TsnDevice {
            @display("p=452,420");
        }
        es06: TsnDevice {
            @display("p=540,420");
        }
        es07: TsnDevice {
            @display("p=628,420");
        }
        es08: TsnDevice {
            @display("p=716,420");
        }
        es09: TsnDevice {
            @display("p=804,420");
        }
        es10: TsnDevice {
            @display("p=892,420");
        }
        sw01: TsnSwitch {
            @display("p=100,240");
        }
        sw02: TsnSwitch {
            @display("p=142,240");
        }
        sw03: TsnSwitch {
            @display("p=184,240");
        }
        sw04: TsnSwitch {
            @display("p=226,240");
        }
        sw05: TsnSwitch {
            @display("p=268,240");
        }
        sw06: TsnSwitch {
            @display("p=310,240");
        }
        sw07: TsnSwitch {
            @display("p=352,240");
        }
        sw08: TsnSwitch {
            @display("p=394,240");
        }
        sw09: TsnSwitch {
            @display("p=436,240");
        }
        sw10: TsnSwitch {
            @display("p=478,240");
        }
        sw11: TsnSwitch {
            @display("p=520,240");
        }
        sw12: TsnSwitch {
            @display("p=562,240");
        }
        sw13: TsnSwitch {
            @display("p=604,240");
        }
        sw14: TsnSwitch {
            @display("p=646,240");
        }
        sw15: TsnSwitch {
            @display("p=688,240");
        }
        sw16: TsnSwitch {
            @display("p=730,240");
        }
        sw17: TsnSwitch {
            @display("p=772,240");
        }
        sw18: TsnSwitch {
            @display("p=814,240");
        }
        sw19: TsnSwitch {
            @display("p=856,240");
        }
        sw20: TsnSwitch {
            @display("p=898,240");
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
        es04.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw06.ethg++; // l_es04_sw06
        es05.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw10.ethg++; // l_es05_sw10
        es06.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw11.ethg++; // l_es06_sw11
        es07.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw15.ethg++; // l_es07_sw15
        es08.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw16.ethg++; // l_es08_sw16
        es09.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw18.ethg++; // l_es09_sw18
        es10.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw20.ethg++; // l_es10_sw20
        sw01.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw02.ethg++; // l_sw01_sw02
        sw01.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw06.ethg++; // l_sw01_sw06
        sw02.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw03.ethg++; // l_sw02_sw03
        sw02.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw07.ethg++; // l_sw02_sw07
        sw02.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw08.ethg++; // l_sw02_sw08
        sw03.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw04.ethg++; // l_sw03_sw04
        sw03.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw08.ethg++; // l_sw03_sw08
        sw04.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw05.ethg++; // l_sw04_sw05
        sw04.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw09.ethg++; // l_sw04_sw09
        sw04.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw10.ethg++; // l_sw04_sw10
        sw05.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw10.ethg++; // l_sw05_sw10
        sw06.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw07.ethg++; // l_sw06_sw07
        sw06.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw11.ethg++; // l_sw06_sw11
        sw07.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw08.ethg++; // l_sw07_sw08
        sw07.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw12.ethg++; // l_sw07_sw12
        sw08.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw09.ethg++; // l_sw08_sw09
        sw08.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw13.ethg++; // l_sw08_sw13
        sw09.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw10.ethg++; // l_sw09_sw10
        sw09.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw14.ethg++; // l_sw09_sw14
        sw10.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw15.ethg++; // l_sw10_sw15
        sw11.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw12.ethg++; // l_sw11_sw12
        sw11.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw16.ethg++; // l_sw11_sw16
        sw11.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw17.ethg++; // l_sw11_sw17
        sw12.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw13.ethg++; // l_sw12_sw13
        sw12.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw17.ethg++; // l_sw12_sw17
        sw13.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw14.ethg++; // l_sw13_sw14
        sw13.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw18.ethg++; // l_sw13_sw18
        sw13.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw19.ethg++; // l_sw13_sw19
        sw14.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw15.ethg++; // l_sw14_sw15
        sw14.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw19.ethg++; // l_sw14_sw19
        sw15.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw20.ethg++; // l_sw15_sw20
        sw16.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw17.ethg++; // l_sw16_sw17
        sw17.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw18.ethg++; // l_sw17_sw18
        sw18.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw19.ethg++; // l_sw18_sw19
        sw19.ethg++ <--> EthernetLink { datarate = 1000000000bps; delay = 0s; } <--> sw20.ethg++; // l_sw19_sw20
}
