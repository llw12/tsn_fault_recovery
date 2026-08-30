package diamond;

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
        destination: TsnDevice {
            @display("p=100,420");
        }
        source: TsnDevice {
            @display("p=900,420");
        }
        s1: TsnSwitch {
            @display("p=100,240");
        }
        s2: TsnSwitch {
            @display("p=366,240");
        }
        s3: TsnSwitch {
            @display("p=632,240");
        }
        s4: TsnSwitch {
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
        s1.ethg++ <--> EthernetLink { datarate = 100000000bps; delay = 0s; } <--> s2.ethg++; // l_s1_s2
        s1.ethg++ <--> EthernetLink { datarate = 100000000bps; delay = 0s; } <--> s3.ethg++; // l_s1_s3
        s2.ethg++ <--> EthernetLink { datarate = 100000000bps; delay = 0s; } <--> s4.ethg++; // l_s2_s4
        s3.ethg++ <--> EthernetLink { datarate = 100000000bps; delay = 0s; } <--> s4.ethg++; // l_s3_s4
        s4.ethg++ <--> EthernetLink { datarate = 100000000bps; delay = 0s; } <--> destination.ethg++; // l_s4_destination
        source.ethg++ <--> EthernetLink { datarate = 100000000bps; delay = 0s; } <--> s1.ethg++; // l_source_s1
}
